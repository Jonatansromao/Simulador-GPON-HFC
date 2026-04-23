# routes_html.py
# -----------------------------
# Rotas HTML (templates)
# -----------------------------
import os
import csv
from io import StringIO
import secrets
import hashlib
import smtplib
import ssl
import json
from urllib import request as urllib_request
from urllib import error as urllib_error
import unicodedata
from collections import defaultdict
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    jsonify,
    render_template,
    session,
    redirect,
    url_for,
    request,
    flash,
    current_app,
    make_response,
)
from models import (
    db,
    Aluno,
    Turma,
    Matricula,
    Questao,
    Resposta,
    Professor,
    QuestaoTurma,
    SimuladoLivre,
    PasswordReset,
    Payment,
)
import random
from statistics import mean
from routes_api import api_login_required_aluno, api_login_required_professor
from extensions import socketio  # ✅ importa socketio daqui, sem ciclo

html_bp = Blueprint("html_bp", __name__)

# -----------------------------
# Configurações úteis
# -----------------------------
# tempo de validade do token (segundos) — padrão 1 hora
RESET_TOKEN_EXPIRY = int(os.getenv("RESET_TOKEN_EXPIRY", "3600"))

try:
    APP_TIMEZONE = ZoneInfo(os.getenv("APP_TIMEZONE", "America/Sao_Paulo"))
except Exception:
    APP_TIMEZONE = timezone(timedelta(hours=-3))


def format_datetime_local(value, fmt: str = "%d/%m/%Y %H:%M") -> str:
    if not value:
        return ""

    if isinstance(value, datetime):
        localized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        return localized.astimezone(APP_TIMEZONE).strftime(fmt)

    return value.strftime(fmt)


def make_no_cache_response(content):
    response = make_response(content)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@html_bp.app_template_filter("datetime_local")
def datetime_local_filter(value, fmt: str = "%d/%m/%Y %H:%M"):
    return format_datetime_local(value, fmt)


def normalize_topic_text(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", str(value))
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(normalized.lower().split())


def normalize_cpf(value: str) -> str:
    if not value:
        return ""
    return "".join(ch for ch in str(value) if ch.isdigit())


def parse_attempt_datetime(value: str):
    raw = (value or "").strip().replace("Z", "")
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def build_question_option_map(questao) -> dict:
    return {
        "A": (getattr(questao, "opcao_a", None) or "").strip(),
        "B": (getattr(questao, "opcao_b", None) or "").strip(),
        "C": (getattr(questao, "opcao_c", None) or "").strip(),
        "D": (getattr(questao, "opcao_d", None) or "").strip(),
    }


def describe_question_answer(questao, answer_value) -> dict:
    raw = str(answer_value or "").strip()
    if not raw:
        return {
            "label": "—",
            "texto": "Não respondida",
            "completa": "Não respondida",
        }

    options = build_question_option_map(questao)
    upper_raw = raw.upper()
    if upper_raw[:1] in options and (len(raw) <= 3 or raw[1:2] in {")", ".", ":", "-"}):
        label = upper_raw[:1]
        option_text = options.get(label) or raw
        return {
            "label": label,
            "texto": option_text,
            "completa": f"{label}) {option_text}" if option_text else label,
        }

    normalized_raw = normalize_topic_text(raw)
    for label, option_text in options.items():
        if option_text and normalize_topic_text(option_text) == normalized_raw:
            return {
                "label": label,
                "texto": option_text,
                "completa": f"{label}) {option_text}",
            }

    if upper_raw[:1] in options and options.get(upper_raw[:1]):
        label = upper_raw[:1]
        option_text = options.get(label) or raw
        return {
            "label": label,
            "texto": option_text,
            "completa": f"{label}) {option_text}",
        }

    return {
        "label": raw,
        "texto": raw,
        "completa": raw,
    }


def build_error_detail(resposta, aluno, questao, tema: str) -> dict:
    resposta_marcada = describe_question_answer(questao, getattr(resposta, "resposta", None))
    resposta_correta = describe_question_answer(questao, getattr(questao, "correta", None))
    data_envio = getattr(resposta, "data_envio", None)

    return {
        "aluno_id": aluno.id,
        "aluno_nome": aluno.nome,
        "questao_id": questao.id,
        "texto": questao.texto,
        "tema": tema,
        "resposta_marcada": resposta_marcada["completa"],
        "resposta_marcada_label": resposta_marcada["label"],
        "resposta_marcada_texto": resposta_marcada["texto"],
        "resposta_correta": resposta_correta["completa"],
        "resposta_correta_label": resposta_correta["label"],
        "resposta_correta_texto": resposta_correta["texto"],
        "data_envio": format_datetime_local(data_envio) if data_envio else None,
    }


def infer_question_theme(questao) -> str:
    tema_explicito = getattr(questao, "tema", None)
    if tema_explicito and str(tema_explicito).strip():
        return str(tema_explicito).strip()

    texto = normalize_topic_text(getattr(questao, "texto", questao or ""))
    banco = ((getattr(questao, "banco", "") or "").strip().upper())

    regras = [
        ("Wi‑Fi e Configuração", ["wifi", "wi-fi", "ssid", "senha", "mesh", "net app", "minha net", "hotspot"]),
        ("Fibra e GPON", ["gpon", "fibra", "optica", "optico", "olt", "ont", "nap", "splitter", "drop flat", "power meter", "pto", "dbm"]),
        ("HFC e Sinal", ["hfc", "coaxial", "cmts", "upstream", "downstream", "mer", "ber", "tilt", "retorno", "impedancia", "cable isolator"]),
        ("TV e Equipamentos", ["decoder", "decodificador", "controle remoto", "now", "telecine", "claro tv", "hdmax", "auto hit", "canal"]),
        ("Telefonia", ["fone", "telefonia", "voip", "chamada", "siga-me", "conferencia"]),
        ("Segurança e Instalação", ["epi", "epc", "altura", "escada", "apr", "seguranca", "instalacao", "instalador"]),
        ("Procedimentos e Atendimento", ["ura", "toa", "os", "procedimento", "cliente", "valida retorno", "baixa", "atendimento", "contrato"]),
    ]

    for tema, palavras_chave in regras:
        if any(palavra in texto for palavra in palavras_chave):
            return tema

    if banco == "GPON":
        return "Fundamentos GPON"
    if banco == "HFC":
        return "Fundamentos HFC"
    return "Geral"


def build_professor_performance_insights(professor_id: int) -> dict:
    respostas = (
        Resposta.query
        .join(Aluno, Resposta.aluno_id == Aluno.id)
        .join(Questao, Resposta.questao_id == Questao.id)
        .filter(Aluno.professor_id == professor_id)
        .order_by(Resposta.data_envio.desc(), Resposta.id.desc())
        .all()
    )

    if not respostas:
        return {
            "total_respostas": 0,
            "temas": [],
            "alunos": [],
            "questoes_criticas": [],
            "tema_padrao": None,
            "alunos_detalhes": {},
            "aluno_padrao": None,
            "erros_detalhados": [],
        }

    temas = defaultdict(
        lambda: {
            "erros": 0,
            "total": 0,
            "alunos": set(),
            "alunos_stats": {},
            "questoes_stats": {},
            "erros_detalhados": [],
        }
    )
    alunos = {}
    questoes = {}
    alunos_detalhes = {}
    erros_detalhados_gerais = []

    for resposta in respostas:
        aluno = resposta.aluno
        questao = resposta.questao
        if not aluno or not questao:
            continue

        tema = infer_question_theme(questao)
        tema_item = temas[tema]
        tema_item["total"] += 1
        tema_item["alunos"].add(aluno.id)

        aluno_item = alunos.setdefault(
            aluno.id,
            {"id": aluno.id, "nome": aluno.nome, "erros": 0, "total": 0, "percentual_erro": 0.0},
        )
        aluno_item["total"] += 1

        aluno_tema_item = tema_item["alunos_stats"].setdefault(
            aluno.id,
            {"id": aluno.id, "nome": aluno.nome, "erros": 0, "total": 0, "percentual_erro": 0.0},
        )
        aluno_tema_item["total"] += 1

        aluno_detalhe = alunos_detalhes.setdefault(
            aluno.id,
            {
                "id": aluno.id,
                "nome": aluno.nome,
                "erros": 0,
                "total": 0,
                "percentual_erro": 0.0,
                "temas": {},
                "questoes_stats": {},
                "erros_detalhados": [],
            },
        )
        aluno_detalhe["total"] += 1

        aluno_tema_detalhe = aluno_detalhe["temas"].setdefault(
            tema,
            {"tema": tema, "erros": 0, "total": 0, "percentual_erro": 0.0},
        )
        aluno_tema_detalhe["total"] += 1

        questao_item = questoes.setdefault(
            questao.id,
            {
                "texto": questao.texto,
                "tema": tema,
                "erros": 0,
                "total": 0,
                "percentual_erro": 0.0,
                "alternativas_erradas": {},
                "ultimo_erro": None,
            },
        )
        questao_item["total"] += 1

        questao_tema_item = tema_item["questoes_stats"].setdefault(
            questao.id,
            {
                "texto": questao.texto,
                "tema": tema,
                "erros": 0,
                "total": 0,
                "percentual_erro": 0.0,
                "alternativas_erradas": {},
                "ultimo_erro": None,
            },
        )
        questao_tema_item["total"] += 1

        aluno_questao_detalhe = aluno_detalhe["questoes_stats"].setdefault(
            questao.id,
            {
                "id": questao.id,
                "texto": questao.texto,
                "tema": tema,
                "erros": 0,
                "total": 0,
                "percentual_erro": 0.0,
                "alternativas_erradas": {},
                "ultimo_erro": None,
                "erros_detalhados": [],
            },
        )
        aluno_questao_detalhe["total"] += 1

        if not bool(resposta.correta):
            erro_detalhado = build_error_detail(resposta, aluno, questao, tema)

            tema_item["erros"] += 1
            aluno_item["erros"] += 1
            aluno_tema_item["erros"] += 1
            questao_item["erros"] += 1
            questao_tema_item["erros"] += 1
            aluno_detalhe["erros"] += 1
            aluno_tema_detalhe["erros"] += 1
            aluno_questao_detalhe["erros"] += 1

            tema_item["erros_detalhados"].append(erro_detalhado)
            aluno_detalhe["erros_detalhados"].append(erro_detalhado)
            aluno_questao_detalhe["erros_detalhados"].append(erro_detalhado)
            erros_detalhados_gerais.append(erro_detalhado)

            for bucket in (questao_item, questao_tema_item, aluno_questao_detalhe):
                bucket["ultimo_erro"] = erro_detalhado
                resposta_chave = erro_detalhado["resposta_marcada"]
                alternativas = bucket.setdefault("alternativas_erradas", {})
                item_alt = alternativas.setdefault(
                    resposta_chave,
                    {"resposta": resposta_chave, "quantidade": 0},
                )
                item_alt["quantidade"] += 1

    temas_ordenados = []
    for tema, valores in temas.items():
        total = valores["total"]
        erros = valores["erros"]

        top_alunos = []
        for aluno_valores in valores["alunos_stats"].values():
            total_aluno = aluno_valores["total"]
            erros_aluno = aluno_valores["erros"]
            registro = {
                **aluno_valores,
                "percentual_erro": round((erros_aluno / total_aluno) * 100, 1) if total_aluno else 0.0,
            }
            if erros_aluno:
                top_alunos.append(registro)
        top_alunos.sort(key=lambda item: (-item["erros"], -item["percentual_erro"], item["nome"]))

        top_questoes = []
        for questao_valores in valores["questoes_stats"].values():
            total_questao = questao_valores["total"]
            erros_questao = questao_valores["erros"]
            alternativas_erradas = sorted(
                questao_valores.get("alternativas_erradas", {}).values(),
                key=lambda item: (-item["quantidade"], item["resposta"]),
            )
            registro = {
                **questao_valores,
                "percentual_erro": round((erros_questao / total_questao) * 100, 1) if total_questao else 0.0,
                "alternativas_erradas": alternativas_erradas[:3],
                "ultimo_erro": questao_valores.get("ultimo_erro"),
            }
            if erros_questao:
                top_questoes.append(registro)
        top_questoes.sort(key=lambda item: (-item["erros"], -item["percentual_erro"], item["texto"]))

        temas_ordenados.append({
            "tema": tema,
            "total": total,
            "erros": erros,
            "alunos_afetados": len(valores["alunos"]),
            "percentual_erro": round((erros / total) * 100, 1) if total else 0.0,
            "top_alunos": top_alunos[:5],
            "top_questoes": top_questoes[:5],
            "erros_detalhados": valores.get("erros_detalhados", [])[:20],
        })
    temas_ordenados.sort(key=lambda item: (-item["erros"], -item["percentual_erro"], item["tema"]))

    alunos_ordenados = []
    for valores in alunos.values():
        total = valores["total"]
        erros = valores["erros"]
        valores["percentual_erro"] = round((erros / total) * 100, 1) if total else 0.0
        alunos_ordenados.append(valores)
    alunos_ordenados.sort(key=lambda item: (-item["erros"], -item["percentual_erro"], item["nome"]))

    alunos_detalhes_formatados = {}
    for aluno_id, valores in alunos_detalhes.items():
        total = valores["total"]
        erros = valores["erros"]

        temas_aluno = []
        for tema_valores in valores["temas"].values():
            total_tema = tema_valores["total"]
            erros_tema = tema_valores["erros"]
            registro = {
                **tema_valores,
                "percentual_erro": round((erros_tema / total_tema) * 100, 1) if total_tema else 0.0,
            }
            if total_tema:
                temas_aluno.append(registro)
        temas_aluno.sort(key=lambda item: (-item["erros"], -item["percentual_erro"], item["tema"]))

        questoes_aluno = []
        for questao_valores in valores["questoes_stats"].values():
            total_questao = questao_valores["total"]
            erros_questao = questao_valores["erros"]
            alternativas_erradas = sorted(
                questao_valores.get("alternativas_erradas", {}).values(),
                key=lambda item: (-item["quantidade"], item["resposta"]),
            )
            registro = {
                **questao_valores,
                "percentual_erro": round((erros_questao / total_questao) * 100, 1) if total_questao else 0.0,
                "alternativas_erradas": alternativas_erradas[:3],
                "ultimo_erro": questao_valores.get("ultimo_erro"),
                "erros_detalhados": (questao_valores.get("erros_detalhados") or [])[:5],
            }
            if erros_questao:
                questoes_aluno.append(registro)
        questoes_aluno.sort(key=lambda item: (-item["erros"], -item["percentual_erro"], item["texto"]))

        alunos_detalhes_formatados[aluno_id] = {
            "id": valores["id"],
            "nome": valores["nome"],
            "erros": erros,
            "total": total,
            "percentual_erro": round((erros / total) * 100, 1) if total else 0.0,
            "temas": temas_aluno[:8],
            "questoes_erradas": questoes_aluno[:10],
            "erros_detalhados": (valores.get("erros_detalhados") or [])[:20],
        }

    questoes_criticas = []
    for valores in questoes.values():
        total = valores["total"]
        erros = valores["erros"]
        valores["percentual_erro"] = round((erros / total) * 100, 1) if total else 0.0
        if erros:
            questoes_criticas.append(valores)
    questoes_criticas.sort(key=lambda item: (-item["erros"], -item["percentual_erro"], item["texto"]))

    return {
        "total_respostas": len(respostas),
        "temas": temas_ordenados[:8],
        "alunos": alunos_ordenados[:10],
        "questoes_criticas": questoes_criticas[:10],
        "tema_padrao": temas_ordenados[0]["tema"] if temas_ordenados else None,
        "alunos_detalhes": alunos_detalhes_formatados,
        "aluno_padrao": alunos_ordenados[0]["id"] if alunos_ordenados else None,
        "erros_detalhados": erros_detalhados_gerais,
    }


def get_selected_theme_insight(performance_insights: dict, selected_theme: str | None = None):
    temas = (performance_insights or {}).get("temas") or []
    if not temas:
        return None

    normalized_selected = normalize_topic_text(selected_theme)
    if normalized_selected:
        for item in temas:
            if normalize_topic_text(item.get("tema")) == normalized_selected:
                return item

    return temas[0]


def get_selected_student_insight(
    performance_insights: dict,
    selected_student: str | None = None,
    selected_theme: str | None = None,
):
    alunos_detalhes = (performance_insights or {}).get("alunos_detalhes") or {}
    if not alunos_detalhes:
        return None

    selected_student = str(selected_student or "").strip()
    if not selected_student.isdigit():
        return None

    aluno = alunos_detalhes.get(int(selected_student))
    if not aluno:
        return None

    resultado = dict(aluno)
    questoes_filtradas = list(resultado.get("questoes_erradas") or [])
    erros_filtrados = list(resultado.get("erros_detalhados") or [])
    tema_detalhe = None
    normalized_theme = normalize_topic_text(selected_theme)

    if normalized_theme:
        tema_detalhe = next(
            (
                item
                for item in (resultado.get("temas") or [])
                if normalize_topic_text(item.get("tema")) == normalized_theme
            ),
            None,
        )
        questoes_filtradas = [
            item for item in questoes_filtradas
            if normalize_topic_text(item.get("tema")) == normalized_theme
        ]
        erros_filtrados = [
            item for item in erros_filtrados
            if normalize_topic_text(item.get("tema")) == normalized_theme
        ]

    resultado["tema_selecionado"] = tema_detalhe
    resultado["questoes_filtradas"] = questoes_filtradas[:10]
    resultado["erros_filtrados"] = erros_filtrados[:20]
    return resultado


def build_export_filename(prefix: str, tema: str | None = None, extension: str = "txt") -> str:
    suffix = normalize_topic_text(tema).replace(" ", "_") if tema else "geral"
    suffix = suffix or "geral"
    return f"{prefix}_{suffix}.{extension}"


def resolve_report_scope_and_theme(performance_insights: dict, scope_param: str | None, tema_param: str | None):
    scope = (scope_param or "geral").strip().lower()
    if scope not in {"geral", "tema"}:
        scope = "geral"

    tema = None
    if scope == "tema" and (tema_param or "").strip():
        tema_base = get_selected_theme_insight(performance_insights, tema_param)
        if tema_base:
            tema = {
                **tema_base,
                "erros_detalhados": [
                    item for item in (performance_insights.get("erros_detalhados") or [])
                    if normalize_topic_text(item.get("tema")) == normalize_topic_text(tema_base.get("tema"))
                ],
            }
        else:
            scope = "geral"

    return scope, tema


# -----------------------------
# Helpers: envio de e-mail e tokens
# -----------------------------
def generate_token(length: int = 48) -> str:
    """Gera token URL-safe. Será enviado por e-mail (token em texto)."""
    return secrets.token_urlsafe(length)


def hash_token(token: str) -> str:
    """Hash do token para armazenamento seguro (sha256 hex)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def send_email(to_address: str, subject: str, body_text: str, body_html: str = None):
    """
    Envia e-mail usando configuração via variáveis de ambiente:
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM, SMTP_USE_SSL (optional, default True if port 465)
    Em dev, se SMTP_DEV_CONSOLE_FALLBACK=true e SMTP_HOST não estiver definido,
    o e-mail será impresso no console (sem entrega real).
    """
    smtp_host = os.getenv("SMTP_HOST") or os.getenv("MAIL_SERVER")
    raw_smtp_port = os.getenv("SMTP_PORT") or os.getenv("MAIL_PORT") or "465"
    try:
        smtp_port = int(str(raw_smtp_port).strip())
    except (TypeError, ValueError):
        print(f"Erro ao enviar e-mail: SMTP_PORT inválido ({raw_smtp_port}).")
        return False
    smtp_user = os.getenv("SMTP_USER") or os.getenv("MAIL_USERNAME")
    smtp_pass = os.getenv("SMTP_PASS") or os.getenv("MAIL_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM") or os.getenv("MAIL_DEFAULT_SENDER") or smtp_user or "no-reply@example.com"

    explicit_ssl = (os.getenv("SMTP_USE_SSL") or os.getenv("MAIL_USE_SSL") or "").lower() in ("1", "true", "yes")
    explicit_tls = (os.getenv("SMTP_USE_TLS") or os.getenv("MAIL_USE_TLS") or "").lower() in ("1", "true", "yes")
    use_ssl = explicit_ssl or (not explicit_tls and smtp_port == 465)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_address
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    if not smtp_host:
        dev_fallback = os.getenv("SMTP_DEV_CONSOLE_FALLBACK", "").lower() in ("1", "true", "yes")
        is_production = os.getenv("RENDER", "").lower() == "true" or os.getenv("APP_ENV", "").lower() == "production"

        if dev_fallback and not is_production:
            print("=== send_email (DEV console fallback, sem entrega real) ===")
            print("To:", to_address)
            print("Subject:", subject)
            print("Body (text):")
            print(body_text)
            if body_html:
                print("Body (html):")
                print(body_html)
            print("=== end ===")
            return True

        print("Erro ao enviar e-mail: SMTP_HOST não configurado.")
        return False

    try:
        if use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
                if smtp_user and smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        else:
            context = ssl.create_default_context()
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                # Usa STARTTLS quando configurado explicitamente ou para portas não-SSL.
                if explicit_tls or smtp_port != 25:
                    server.starttls(context=context)
                if smtp_user and smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        return True
    except Exception as e:
        # Em produção, use logger
        print("Erro ao enviar e-mail:", e)
        return send_email_via_brevo_api(to_address, subject, body_text, body_html)


def send_email_via_brevo_api(to_address: str, subject: str, body_text: str, body_html: str = None) -> bool:
    """
    Fallback via API HTTP da Brevo quando SMTP falhar (ex.: timeout de rede na porta 587/465).
    Requer BREVO_API_KEY.
    """
    api_key = os.getenv("BREVO_API_KEY") or os.getenv("SENDINBLUE_API_KEY")
    if not api_key:
        return False

    sender_email = os.getenv("BREVO_SENDER_EMAIL") or os.getenv("SMTP_FROM") or "no-reply@example.com"
    sender_name = os.getenv("BREVO_SENDER_NAME") or "Simulador HFC/GPON"

    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": to_address}],
        "subject": subject,
        "textContent": body_text,
        "htmlContent": body_html or f"<pre>{body_text}</pre>",
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=data,
        method="POST",
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "api-key": api_key,
        },
    )

    try:
        with urllib_request.urlopen(req, timeout=15) as response:
            status = getattr(response, "status", 200)
            return 200 <= int(status) < 300
    except urllib_error.HTTPError as e:
        try:
            details = e.read().decode("utf-8", errors="ignore")
        except Exception:
            details = ""
        print(f"Erro ao enviar e-mail via Brevo API: HTTP {e.code} {details}")
        return False
    except Exception as e:
        print("Erro ao enviar e-mail via Brevo API:", e)
        return False


def create_password_reset(user_type: str, user_id: int, email: str, nome: str) -> bool:
    """
    Gera token, salva hash no banco e envia e-mail com link de reset.
    user_type: 'aluno' ou 'professor'
    """
    try:
        token = generate_token()
        token_hash = hash_token(token)

        # salva no banco
        pr = PasswordReset(token_hash=token_hash, user_type=user_type, user_id=user_id)
        db.session.add(pr)
        db.session.commit()

        reset_url = url_for("html_bp.reset_password", token=token, _external=True)

        subject = "Redefinição de senha - Simulador HFC/GPON"
        body_text = f"""Olá {nome},

Você solicitou a redefinição de senha. Acesse o link abaixo para escolher uma nova senha. O link expira em {RESET_TOKEN_EXPIRY//60} minutos.

{reset_url}

Se você não solicitou, ignore esta mensagem.

Atenciosamente,
Equipe Simulador HFC/GPON
"""
        body_html = f"""
<p>Olá {nome},</p>
<p>Você solicitou a redefinição de senha. Acesse o link abaixo para escolher uma nova senha. O link expira em <strong>{RESET_TOKEN_EXPIRY//60} minutos</strong>.</p>
<p><a href="{reset_url}">Redefinir minha senha</a></p>
<p>Se você não solicitou, ignore esta mensagem.</p>
<p>Atenciosamente,<br>Equipe Simulador HFC/GPON</p>
"""
        sent = send_email(email, subject, body_text, body_html)
        if not sent:
            try:
                pr.used = True
                db.session.commit()
            except Exception:
                db.session.rollback()
        return sent
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao criar fluxo de recuperação de senha.")
        return False


def validate_token(token: str) -> PasswordReset or None:
    """Valida token: calcula hash, busca registro não usado e dentro do expiry."""
    if not token:
        return None
    token_hash = hash_token(token)
    pr = PasswordReset.query.filter_by(token_hash=token_hash, used=False).first()
    if not pr:
        return None
    # verifica expiracao
    age = datetime.utcnow() - pr.created_at
    if age.total_seconds() > RESET_TOKEN_EXPIRY:
        return None
    return pr


# -----------------------------
# Decorator para verificar premium
# -----------------------------
from functools import wraps

def premium_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "usuario" not in session or session["usuario"]["tipo"] != "professor":
            flash("Acesso negado.", "danger")
            return redirect(url_for("html_bp.login_professor"))
        professor = Professor.query.get(session["usuario"]["id"])
        if not professor or not professor.is_premium_active():
            flash("Esta funcionalidade requer assinatura premium. Assine agora por R$ 250/mês.", "warning")
            return redirect(url_for("html_bp.professor_premium"))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "usuario" not in session or session["usuario"]["tipo"] != "professor":
            flash("Acesso restrito ao administrador.", "danger")
            return redirect(url_for("html_bp.login_professor"))

        professor = Professor.query.get(session["usuario"]["id"])
        if not professor or not (professor.is_admin or is_admin_email(professor.email)):
            flash("Apenas o administrador pode acessar este painel.", "warning")
            return redirect(url_for("html_bp.professor_dashboard"))

        return f(*args, **kwargs)
    return decorated_function


# -----------------------------
# Funções utilitárias de atualização em tempo real
# -----------------------------
def build_turma_realtime_payload(turma):
    alunos_data = [
        {
            "aluno_id": m.aluno_id,
            "nome": m.aluno.nome,
            "email": getattr(m.aluno, "email", ""),
            "pronto": bool(m.pronto),
        }
        for m in turma.matriculas
    ]
    return {
        "id": turma.id,
        "status": turma.status,
        "prontos": sum(1 for m in turma.matriculas if m.pronto),
        "total": len(turma.matriculas),
        "alunos": alunos_data,
        "auto_restart_enabled": bool(getattr(turma, "auto_restart_enabled", False)),
    }


def emitir_atualizacao_turma(turma):
    payload = build_turma_realtime_payload(turma)
    socketio.emit("status_turma_atualizado", payload)
    socketio.emit("alunos_prontos_atualizado", payload)
    return payload


def resetar_ciclo_automatico_turma(turma):
    if not turma or not bool(getattr(turma, "auto_restart_enabled", False)):
        return False

    alterou = False
    if turma.status != "Aguardando":
        turma.status = "Aguardando"
        alterou = True

    for matricula in turma.matriculas:
        if matricula.pronto:
            matricula.pronto = False
            alterou = True

    return alterou


def iniciar_turma_se_todos_prontos(turma):
    if not turma or not bool(getattr(turma, "auto_restart_enabled", False)):
        return False

    matriculas = list(turma.matriculas)
    if not matriculas:
        return False

    if turma.status == "Aguardando" and all(bool(m.pronto) for m in matriculas):
        turma.status = "Em andamento"
        return True

    return False


def atualizar_status_turma(turma_id, novo_status):
    turma = Turma.query.get(turma_id)
    if turma:
        turma.status = novo_status
        db.session.commit()
        emitir_atualizacao_turma(turma)


def build_professor_turma_action_response(turma, message: str, category: str = "info"):
    payload = emitir_atualizacao_turma(turma)
    emit_professor_dashboard_update(turma.professor_id)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "message": message, **payload})

    flash(message, category)
    next_url = request.form.get("next") or request.args.get("next")
    if next_url and next_url.startswith("/"):
        return redirect(next_url)

    referrer = request.referrer or ""
    if f"/professor/sala/{turma.id}" in referrer:
        return redirect(url_for("html_bp.professor_sala", turma_id=turma.id))

    return redirect(url_for("html_bp.professor_dashboard"))


@html_bp.route("/")
def home():
    """Página inicial"""
    return render_template("home.html")


@html_bp.route("/home")
def home_alias():
    return redirect(url_for("html_bp.home"))


@html_bp.route("/termos")
@html_bp.route("/termos-de-uso")
def termos_uso():
    """Página pública de termos de uso."""
    return render_template("termos.html")


@html_bp.route("/privacidade")
@html_bp.route("/politica-de-privacidade")
def politica_privacidade():
    """Página pública de política de privacidade."""
    return render_template("privacidade.html")


# -----------------------------
# Rotas de registro
# -----------------------------
ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.getenv("ADMIN_EMAILS", "jonatansilvaromao@gmail.com").split(",")
    if email.strip()
}


def is_admin_email(email: str) -> bool:
    return bool(email and email.strip().lower() in ADMIN_EMAILS)


@html_bp.app_context_processor
def inject_admin_session_flag():
    usuario = session.get("usuario") or {}
    professor = None
    if usuario.get("tipo") == "professor" and usuario.get("id"):
        professor = Professor.query.get(usuario.get("id"))

    return {
        "is_admin_session": bool(professor and (professor.is_admin or is_admin_email(professor.email))),
    }


def generate_invite_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "HGP-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not Professor.query.filter_by(invite_code=code).first():
            return code


def ensure_professor_invite_code(professor: Professor | None) -> str | None:
    if not professor:
        return None
    if not professor.invite_code:
        professor.invite_code = generate_invite_code()
        db.session.commit()
    return professor.invite_code


def emit_professor_dashboard_update(professor_id: int | None):
    if professor_id:
        socketio.emit("painel_professor_atualizado", {"professor_id": professor_id})


def get_aluno_status_message(aluno: Aluno | None) -> tuple[str | None, str]:
    if not aluno:
        return None, "secondary"

    status = (aluno.approval_status or "approved").lower()
    if status == "pending":
        return "Seu cadastro está pendente de aprovação do professor. Assim que ele aprovar, o acesso completo será liberado.", "warning"
    if status == "rejected":
        return "Seu cadastro foi recusado pelo professor responsável. Se necessário, solicite um novo código de convite.", "danger"
    return None, "success"


@html_bp.route("/professor/register", methods=["GET", "POST"])
def professor_register():
    if request.method == "POST":
        nome = (request.form.get("username") or "").strip()  # campo do formulário
        email = (request.form.get("email") or "").strip().lower()  # se você quiser pedir email também
        senha = request.form.get("password")

        # Verifica se já existe professor com esse email
        existente = Professor.query.filter_by(email=email).first()
        if existente:
            return render_template("professor_register.html", message="Email já cadastrado!")

        # Cria novo professor sem acesso premium
        novo_professor = Professor(nome=nome, email=email, invite_code=generate_invite_code())
        novo_professor.set_password(senha)  # usa o método para gerar hash
        novo_professor.is_admin = is_admin_email(email)
        novo_professor.is_premium = novo_professor.is_admin
        novo_professor.premium_expires_at = None
        db.session.add(novo_professor)
        db.session.commit()

        flash("Cadastro realizado com sucesso! Faça login e assine premium para liberar todas as funcionalidades.", "success")
        return redirect(url_for("html_bp.login_professor"))

    return render_template("professor_register.html")


@html_bp.route("/aluno/register", methods=["GET", "POST"])
def aluno_register():
    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        cpf = normalize_cpf(request.form.get("cpf") or "")
        empresa = (request.form.get("empresa") or "").strip()
        codigo_convite = (request.form.get("codigo_convite") or "").strip().upper()
        email = (request.form.get("email") or "").strip().lower()
        senha = request.form.get("senha")

        if not empresa or not codigo_convite:
            flash("Informe a empresa e um código de convite válido para continuar.", "warning")
            return redirect(url_for("html_bp.aluno_register"))

        professor = Professor.query.filter_by(invite_code=codigo_convite).first()
        if not professor:
            flash("Código de convite inválido. Verifique com seu professor e tente novamente.", "danger")
            return redirect(url_for("html_bp.aluno_register"))

        # Verifica se já existe aluno com mesmo email ou CPF
        if Aluno.query.filter((Aluno.email == email) | (Aluno.cpf == cpf)).first():
            flash("Já existe um aluno com este CPF ou e-mail.", "danger")
            return redirect(url_for("html_bp.aluno_register"))

        aluno = Aluno(
            nome=nome,
            cpf=cpf,
            email=email,
            empresa=empresa,
            professor_id=professor.id,
            approval_status="pending",
            invite_code_used=codigo_convite,
        )
        aluno.set_password(senha)
        db.session.add(aluno)
        db.session.commit()
        emit_professor_dashboard_update(professor.id)

        flash("Cadastro realizado com sucesso! Seu acesso ficará pendente até a aprovação do professor.", "success")
        return redirect(url_for("html_bp.login_aluno"))

    return render_template("aluno_register.html")


# -----------------------------
# Rotas de dashboard
# -----------------------------
@html_bp.route("/perfil", methods=["GET", "POST"])
def perfil():
    if "usuario" not in session:
        flash("Faça login para acessar seu perfil.", "warning")
        return redirect(url_for("html_bp.login_aluno"))

    tipo = session["usuario"]["tipo"]
    if tipo == "aluno":
        user = Aluno.query.get(session["usuario"]["id"])
    else:
        user = Professor.query.get(session["usuario"]["id"])

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Nome não pode ficar em branco.", "danger")
            return redirect(url_for("html_bp.perfil"))

        user.nome = nome
        session["usuario"]["nome"] = nome

        senha_atual = request.form.get("senha_atual", "")
        nova_senha = request.form.get("nova_senha", "")
        confirma_senha = request.form.get("confirma_senha", "")

        if nova_senha or confirma_senha or senha_atual:
            if not senha_atual or not user.check_password(senha_atual):
                flash("Senha atual incorreta.", "danger")
                return redirect(url_for("html_bp.perfil"))
            if nova_senha != confirma_senha:
                flash("Nova senha e confirmação não conferem.", "danger")
                return redirect(url_for("html_bp.perfil"))
            if len(nova_senha) < 6:
                flash("A nova senha deve ter pelo menos 6 caracteres.", "danger")
                return redirect(url_for("html_bp.perfil"))
            user.set_password(nova_senha)

        db.session.commit()
        flash("Perfil atualizado com sucesso.", "success")
        return redirect(url_for("html_bp.perfil"))

    return render_template("perfil.html", user=user, tipo=tipo)


@html_bp.route("/professor/dashboard", methods=["GET", "POST"])
@api_login_required_professor
@premium_required
def professor_dashboard():
    professor_id = session["usuario"]["id"]
    professor_nome = session["usuario"]["nome"]

    if request.method == "POST":
        modo = (request.form.get("modo") or "").strip().lower()
        nome_turma = (request.form.get("turma") or "").strip()
        disciplina = (request.form.get("disciplina") or "").strip()
        data_str = (request.form.get("data") or "").strip()
        banco = (request.form.get("sheet_name") or "").strip().upper()

        if not nome_turma or not disciplina or not data_str or banco not in {"HFC", "GPON"}:
            flash("Preencha corretamente os dados da turma e escolha um banco válido.", "danger")
            return redirect(url_for("html_bp.professor_dashboard"))

        # Converte a data de string (YYYY-MM-DD) para objeto datetime.date
        from datetime import datetime

        try:
            data_obj = datetime.strptime(data_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Data inválida. Use o formato AAAA-MM-DD.", "danger")
            return redirect(url_for("html_bp.professor_dashboard"))

        try:
            turma = Turma(
                nome=nome_turma,
                disciplina=disciplina,
                data=data_obj,
                professor_id=professor_id,
                status="Aguardando",
                sheet_name=banco,
                auto_restart_enabled=bool(request.form.get("auto_restart_enabled")),
            )
            db.session.add(turma)
            db.session.flush()

            if modo == "aleatoria":
                questoes_selecionadas = (
                    Questao.query.filter_by(banco=banco)
                    .order_by(db.func.random())
                    .limit(20)
                    .all()
                )
                if not questoes_selecionadas:
                    db.session.rollback()
                    flash(f"Nenhuma questão encontrada no banco {banco}.", "warning")
                    return redirect(url_for("html_bp.professor_dashboard"))

            elif modo == "manual":
                ids = [
                    int(q_id)
                    for q_id in request.form.getlist("questoes")
                    if str(q_id).strip().isdigit()
                ]
                if not ids:
                    db.session.rollback()
                    flash("Selecione ao menos uma questão para criar a turma manual.", "warning")
                    return redirect(url_for("html_bp.professor_dashboard"))

                questoes_selecionadas = Questao.query.filter(
                    Questao.id.in_(ids),
                    Questao.banco == banco,
                ).all()
                if not questoes_selecionadas:
                    db.session.rollback()
                    flash("Não foi possível carregar as questões selecionadas.", "danger")
                    return redirect(url_for("html_bp.professor_dashboard"))
            else:
                db.session.rollback()
                flash("Modo de criação de turma inválido.", "danger")
                return redirect(url_for("html_bp.professor_dashboard"))

            turma.questoes.extend(questoes_selecionadas)
            db.session.commit()
            flash(
                f"Turma '{nome_turma}' criada com sucesso com {len(questoes_selecionadas)} questão(ões).",
                "success",
            )
            return redirect(url_for("html_bp.professor_dashboard"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Erro ao criar turma no painel do professor.")
            flash("Ocorreu um erro interno ao criar a turma. Tente novamente.", "danger")
            return redirect(url_for("html_bp.professor_dashboard"))

    turmas = Turma.query.filter_by(professor_id=professor_id).all()

    # 🔹 Calcula prontos e total de alunos
    for turma in turmas:
        turma.prontos = sum(1 for m in turma.matriculas if m.pronto)
        turma.alunos = turma.matriculas

    professor = Professor.query.get(professor_id)
    invite_code = ensure_professor_invite_code(professor)
    subscription_status = "Free"
    expires_at = None
    solicitacoes_pendentes = []
    alunos_vinculados = []
    total_alunos_aprovados = 0
    if professor:
        solicitacoes_pendentes = Aluno.query.filter_by(
            professor_id=professor.id,
            approval_status="pending",
        ).order_by(Aluno.nome.asc()).all()
        alunos_vinculados = Aluno.query.filter_by(
            professor_id=professor.id,
        ).order_by(Aluno.nome.asc()).all()
        total_alunos_aprovados = Aluno.query.filter(
            Aluno.professor_id == professor.id,
            Aluno.approval_status.in_(["approved", None]),
        ).count()

        if professor.is_admin:
            subscription_status = "VIP Vitalício"
            expires_at = "Vitalício"
        elif professor.is_premium_active():
            subscription_status = "Premium"
            expires_at = professor.premium_expires_at.strftime("%d/%m/%Y") if professor.premium_expires_at else None

    performance_insights = build_professor_performance_insights(professor_id)
    tema_param = (request.args.get("tema") or "").strip()
    aluno_param = (request.args.get("aluno") or "").strip()

    selected_theme = get_selected_theme_insight(
        performance_insights,
        tema_param,
    )
    selected_student = get_selected_student_insight(
        performance_insights,
        aluno_param,
        tema_param,
    )

    return render_template(
        "professor_dashboard.html",
        nome=professor_nome,
        turmas=turmas,
        subscription_status=subscription_status,
        expires_at=expires_at,
        invite_code=invite_code,
        solicitacoes_pendentes=solicitacoes_pendentes,
        alunos_vinculados=alunos_vinculados,
        total_alunos_aprovados=total_alunos_aprovados,
        performance_insights=performance_insights,
        selected_theme=selected_theme,
        selected_student=selected_student,
    )


@html_bp.route("/professor/relatorio_temas/excel")
@api_login_required_professor
@premium_required
def professor_relatorio_temas_excel():
    professor_id = session["usuario"]["id"]
    professor = Professor.query.get(professor_id)
    performance_insights = build_professor_performance_insights(professor_id)
    tema_param = (request.args.get("tema") or "").strip()
    scope_param, tema_selecionado = resolve_report_scope_and_theme(
        performance_insights,
        request.args.get("scope"),
        tema_param,
    )

    content = render_template(
        "professor_relatorio_excel.html",
        professor=professor,
        performance_insights=performance_insights,
        tema_selecionado=tema_selecionado,
        report_scope=scope_param,
        generated_at=format_datetime_local(datetime.utcnow()),
    )

    response = make_response(content)
    response.headers["Content-Type"] = "application/vnd.ms-excel; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f"attachment; filename={build_export_filename('relatorio_temas', (tema_selecionado or {}).get('tema') if scope_param == 'tema' else None, 'xls')}"
    )
    return response


@html_bp.route("/professor/relatorio_temas/pdf")
@api_login_required_professor
@premium_required
def professor_relatorio_temas_pdf():
    professor_id = session["usuario"]["id"]
    professor = Professor.query.get(professor_id)
    performance_insights = build_professor_performance_insights(professor_id)
    tema_param = (request.args.get("tema") or "").strip()
    scope_param, tema_selecionado = resolve_report_scope_and_theme(
        performance_insights,
        request.args.get("scope"),
        tema_param,
    )

    return render_template(
        "professor_relatorio_pdf.html",
        professor=professor,
        performance_insights=performance_insights,
        tema_selecionado=tema_selecionado,
        report_scope=scope_param,
        generated_at=format_datetime_local(datetime.utcnow()),
    )


@html_bp.route("/professor/relatorio_temas/csv")
@api_login_required_professor
@premium_required
def professor_relatorio_temas_csv():
    professor_id = session["usuario"]["id"]
    professor = Professor.query.get(professor_id)
    performance_insights = build_professor_performance_insights(professor_id)
    tema_param = (request.args.get("tema") or "").strip()
    scope_param, tema_selecionado = resolve_report_scope_and_theme(
        performance_insights,
        request.args.get("scope"),
        tema_param,
    )

    output = StringIO()
    writer = csv.writer(output, delimiter=";")

    tema_nome = (tema_selecionado or {}).get("tema") if scope_param == "tema" else "Geral"
    writer.writerow(["Relatorio de dificuldades por tema"])
    writer.writerow(["Professor", professor.nome if professor else "-"])
    writer.writerow(["Escopo", "Tema selecionado" if scope_param == "tema" else "Geral"])
    writer.writerow(["Tema", tema_nome])
    writer.writerow(["Gerado em", format_datetime_local(datetime.utcnow())])
    writer.writerow([])

    writer.writerow(["Resumo por tema"])
    writer.writerow(["Tema", "Erros", "Total", "Taxa de erro (%)", "Alunos afetados"])
    temas_base = [tema_selecionado] if (scope_param == "tema" and tema_selecionado) else (performance_insights.get("temas") or [])
    for item in temas_base:
        writer.writerow([
            item.get("tema"),
            item.get("erros", 0),
            item.get("total", 0),
            item.get("percentual_erro", 0),
            item.get("alunos_afetados", 0),
        ])

    writer.writerow([])
    writer.writerow(["Detalhamento de erros"])
    writer.writerow(["Aluno", "Tema", "Pergunta", "Resposta marcada", "Resposta correta", "Data do erro"])

    erros_detalhados = (
        (tema_selecionado or {}).get("erros_detalhados")
        if scope_param == "tema"
        else (performance_insights.get("erros_detalhados") or [])
    ) or []
    for erro in erros_detalhados:
        writer.writerow([
            erro.get("aluno_nome"),
            erro.get("tema"),
            erro.get("texto"),
            erro.get("resposta_marcada"),
            erro.get("resposta_correta"),
            erro.get("data_envio") or "-",
        ])

    csv_content = "\ufeff" + output.getvalue()
    output.close()

    response = make_response(csv_content)
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f"attachment; filename={build_export_filename('relatorio_temas', (tema_selecionado or {}).get('tema') if scope_param == 'tema' else None, 'csv')}"
    )
    return response


@html_bp.route("/admin/dashboard")
@api_login_required_professor
@admin_required
def admin_dashboard():
    total_professores = Professor.query.count()
    total_alunos = Aluno.query.count()
    total_turmas = Turma.query.count()
    premium_ativos = sum(1 for professor in Professor.query.all() if professor.is_premium_active())
    alunos_pendentes = Aluno.query.filter_by(approval_status="pending").order_by(Aluno.nome.asc()).all()

    pagamentos_pendentes = []
    pagamentos_vistos = set()
    for pagamento in Payment.query.filter(
        Payment.status.in_(["pending", "processing", "in_process"])
    ).order_by(Payment.created_at.desc()).all():
        if pagamento.professor_id in pagamentos_vistos:
            continue
        pagamentos_pendentes.append(pagamento)
        pagamentos_vistos.add(pagamento.professor_id)
        if len(pagamentos_pendentes) >= 10:
            break

    professores_data = []
    for professor in Professor.query.order_by(Professor.nome.asc()).all():
        premium_expires_at = professor.premium_expires_at
        is_trial_active = bool(
            professor.is_premium_active()
            and premium_expires_at
            and premium_expires_at <= datetime.utcnow() + timedelta(days=3, minutes=5)
        )

        professores_data.append({
            "id": professor.id,
            "nome": professor.nome,
            "email": professor.email,
            "is_admin": professor.is_admin or is_admin_email(professor.email),
            "premium_active": professor.is_premium_active(),
            "premium_expires_at": premium_expires_at,
            "is_trial_active": is_trial_active,
            "total_turmas": len(professor.turmas),
            "total_alunos": Aluno.query.filter_by(professor_id=professor.id).count(),
            "pendentes": Aluno.query.filter_by(professor_id=professor.id, approval_status="pending").count(),
        })

    banco_filtro = (request.args.get("banco") or "todos").strip().upper()
    if banco_filtro not in {"HFC", "GPON"}:
        banco_filtro = "TODOS"

    busca_texto = (request.args.get("busca") or "").strip()
    tema_filtro = (request.args.get("tema") or "todos").strip()
    tema_filtro_normalizado = "TODOS" if tema_filtro.lower() == "todos" else tema_filtro
    pagina_atual = request.args.get("page", default=1, type=int) or 1
    itens_por_pagina = 20

    questoes_query = Questao.query
    if banco_filtro in {"HFC", "GPON"}:
        questoes_query = questoes_query.filter(Questao.banco == banco_filtro)

    temas_disponiveis = [
        tema
        for (tema,) in (
            questoes_query
            .with_entities(Questao.tema)
            .filter(Questao.tema.isnot(None))
            .filter(db.func.trim(Questao.tema) != "")
            .distinct()
            .order_by(Questao.tema.asc())
            .all()
        )
        if tema
    ]

    if tema_filtro_normalizado != "TODOS":
        questoes_query = questoes_query.filter(
            db.func.lower(db.func.trim(Questao.tema)) == tema_filtro_normalizado.lower()
        )

    if busca_texto:
        termo = f"%{busca_texto}%"
        questoes_query = questoes_query.filter(
            db.or_(
                Questao.texto.ilike(termo),
                Questao.tema.ilike(termo),
            )
        )

    paginacao_questoes = questoes_query.order_by(Questao.id.desc()).paginate(
        page=pagina_atual,
        per_page=itens_por_pagina,
        error_out=False,
    )
    questoes_recentes = paginacao_questoes.items

    current_query_params = {
        "banco": banco_filtro if banco_filtro in {"HFC", "GPON"} else "todos",
        "tema": tema_filtro_normalizado if tema_filtro_normalizado != "TODOS" else "todos",
        "busca": busca_texto,
        "page": pagina_atual,
    }
    current_admin_url = url_for("html_bp.admin_dashboard", **current_query_params)

    return render_template(
        "admin_dashboard.html",
        total_professores=total_professores,
        total_alunos=total_alunos,
        total_turmas=total_turmas,
        premium_ativos=premium_ativos,
        alunos_pendentes=alunos_pendentes,
        pagamentos_pendentes=pagamentos_pendentes,
        professores=professores_data,
        questoes_recentes=questoes_recentes,
        paginacao_questoes=paginacao_questoes,
        banco_filtro=banco_filtro,
        tema_filtro=tema_filtro_normalizado,
        temas_disponiveis=temas_disponiveis,
        busca_texto=busca_texto,
        current_admin_url=current_admin_url,
    )


@html_bp.route("/admin/professores/<int:professor_id>/premium", methods=["POST"])
@api_login_required_professor
@admin_required
def admin_toggle_premium(professor_id):
    professor = Professor.query.get_or_404(professor_id)
    action = (request.form.get("action") or "toggle").strip().lower()

    if professor.is_admin or is_admin_email(professor.email):
        flash("A conta administradora mantém acesso premium permanente.", "info")
        return redirect(url_for("html_bp.admin_dashboard"))

    if action == "remover":
        professor.is_premium = False
        professor.premium_expires_at = None
        message = f"Premium removido do professor {professor.nome}."
    elif action in {"teste_3_dias", "trial_3_days", "teste"}:
        base_date = professor.premium_expires_at if professor.premium_expires_at and professor.premium_expires_at > datetime.utcnow() else datetime.utcnow()
        professor.is_premium = True
        professor.premium_expires_at = base_date + timedelta(days=3)
        message = f"Teste premium de 3 dias ativado para {professor.nome} até {format_datetime_local(professor.premium_expires_at, '%d/%m/%Y %H:%M')}."
    else:
        base_date = professor.premium_expires_at if professor.premium_expires_at and professor.premium_expires_at > datetime.utcnow() else datetime.utcnow()
        professor.is_premium = True
        professor.premium_expires_at = base_date + timedelta(days=30)
        message = f"Premium ativado/estendido para {professor.nome} até {format_datetime_local(professor.premium_expires_at, '%d/%m/%Y %H:%M')}."

    db.session.commit()
    flash(message, "success")
    return redirect(url_for("html_bp.admin_dashboard"))


@html_bp.route("/admin/questoes/adicionar", methods=["POST"])
@api_login_required_professor
@admin_required
def admin_add_question():
    banco = (request.form.get("banco") or "").strip().upper()
    texto = (request.form.get("texto") or "").strip()
    opcao_a = (request.form.get("opcao_a") or "").strip()
    opcao_b = (request.form.get("opcao_b") or "").strip()
    opcao_c = (request.form.get("opcao_c") or "").strip()
    opcao_d = (request.form.get("opcao_d") or "").strip()
    correta = (request.form.get("correta") or "").strip().upper()
    tema = (request.form.get("tema") or "").strip()
    imagem = (request.form.get("imagem") or "").strip()

    next_url = (request.form.get("next") or "").strip()

    def redirect_admin_default():
        if next_url.startswith("/"):
            return redirect(next_url)
        return redirect(url_for("html_bp.admin_dashboard"))

    if banco not in {"HFC", "GPON"}:
        flash("Selecione um banco valido (HFC ou GPON).", "warning")
        return redirect_admin_default()

    if not texto:
        flash("A pergunta nao pode ficar vazia.", "warning")
        return redirect_admin_default()

    if not all([opcao_a, opcao_b, opcao_c, opcao_d]):
        flash("Preencha todas as alternativas A, B, C e D.", "warning")
        return redirect_admin_default()

    if correta not in {"A", "B", "C", "D"}:
        flash("A alternativa correta deve ser A, B, C ou D.", "warning")
        return redirect_admin_default()

    duplicada = Questao.query.filter(
        Questao.banco == banco,
        db.func.lower(db.func.trim(Questao.texto)) == texto.lower(),
    ).first()
    if duplicada:
        flash("Essa pergunta ja existe no banco selecionado.", "info")
        return redirect_admin_default()

    nova_questao = Questao(
        texto=texto,
        opcao_a=opcao_a,
        opcao_b=opcao_b,
        opcao_c=opcao_c,
        opcao_d=opcao_d,
        correta=correta,
        imagem=imagem or None,
        banco=banco,
        tema=tema or None,
    )

    db.session.add(nova_questao)
    db.session.commit()

    flash(f"Pergunta adicionada com sucesso no banco {banco}.", "success")
    return redirect_admin_default()


@html_bp.route("/admin/questoes/<int:questao_id>/editar", methods=["POST"])
@api_login_required_professor
@admin_required
def admin_edit_question(questao_id):
    questao = Questao.query.get_or_404(questao_id)
    next_url = (request.form.get("next") or "").strip()

    def redirect_admin_default():
        if next_url.startswith("/"):
            return redirect(next_url)
        return redirect(url_for("html_bp.admin_dashboard"))

    banco = (request.form.get("banco") or "").strip().upper()
    texto = (request.form.get("texto") or "").strip()
    opcao_a = (request.form.get("opcao_a") or "").strip()
    opcao_b = (request.form.get("opcao_b") or "").strip()
    opcao_c = (request.form.get("opcao_c") or "").strip()
    opcao_d = (request.form.get("opcao_d") or "").strip()
    correta = (request.form.get("correta") or "").strip().upper()
    tema = (request.form.get("tema") or "").strip()
    imagem = (request.form.get("imagem") or "").strip()

    if banco not in {"HFC", "GPON"}:
        flash("Selecione um banco valido (HFC ou GPON).", "warning")
        return redirect_admin_default()

    if not texto:
        flash("A pergunta nao pode ficar vazia.", "warning")
        return redirect_admin_default()

    if not all([opcao_a, opcao_b, opcao_c, opcao_d]):
        flash("Preencha todas as alternativas A, B, C e D.", "warning")
        return redirect_admin_default()

    if correta not in {"A", "B", "C", "D"}:
        flash("A alternativa correta deve ser A, B, C ou D.", "warning")
        return redirect_admin_default()

    duplicada = Questao.query.filter(
        Questao.id != questao.id,
        Questao.banco == banco,
        db.func.lower(db.func.trim(Questao.texto)) == texto.lower(),
    ).first()
    if duplicada:
        flash("Ja existe outra pergunta igual nesse banco.", "info")
        return redirect_admin_default()

    questao.banco = banco
    questao.texto = texto
    questao.opcao_a = opcao_a
    questao.opcao_b = opcao_b
    questao.opcao_c = opcao_c
    questao.opcao_d = opcao_d
    questao.correta = correta
    questao.tema = tema or None
    questao.imagem = imagem or None

    db.session.commit()
    flash(f"Pergunta #{questao.id} atualizada com sucesso.", "success")
    return redirect_admin_default()


@html_bp.route("/aluno/dashboard", methods=["GET", "POST"])
def aluno_dashboard():
    # ✅ Verifica se o aluno está logado
    if "usuario" not in session or session["usuario"]["tipo"] != "aluno":
        return redirect(url_for("html_bp.login_aluno"))

    aluno = Aluno.query.get(session["usuario"]["id"])
    approval_message, approval_category = get_aluno_status_message(aluno)

    # ✅ Entrar em turma
    if request.method == "POST":
        if not aluno.is_approved():
            flash(approval_message or "Seu acesso ainda não foi aprovado pelo professor.", approval_category)
            return redirect(url_for("html_bp.aluno_dashboard"))

        codigo_turma = request.form.get("turma")
        turma = Turma.query.filter_by(nome=codigo_turma).first()
        if turma:
            matricula = Matricula.query.filter_by(aluno_id=aluno.id, turma_id=turma.id).first()
            if not matricula:
                nova_matricula = Matricula(aluno_id=aluno.id, turma_id=turma.id)
                db.session.add(nova_matricula)
                db.session.commit()
            # 🔹 Agora redireciona direto para a sala de espera
            return redirect(url_for("html_bp.sala_espera", turma_id=turma.id))
        else:
            flash("Turma não encontrada.", "danger")

    # ✅ Turmas do aluno
    turmas = [m.turma for m in aluno.matriculas] if aluno.is_approved() else []

    # ✅ Histórico detalhado
    historico = []
    if aluno.is_approved():
        respostas = Resposta.query.filter_by(aluno_id=aluno.id).order_by(Resposta.data_envio.desc()).all()
        for r in respostas:
            questao = Questao.query.get(r.questao_id) if hasattr(r, "questao_id") else None
            historico.append(
                {
                    "questao_id": r.questao_id if hasattr(r, "questao_id") else None,
                    "questao": questao.texto if questao else r.questao,
                    "resposta": r.resposta,
                    "correta": r.correta,
                    "questao_correta": questao.correta if questao else None,
                    "data_envio": r.data_envio,
                }
            )

    return render_template(
        "aluno_dashboard.html",
        nome=aluno.nome,
        turmas=turmas,
        historico=historico,
        approval_status=(aluno.approval_status or "approved").lower(),
        approval_message=approval_message,
        professor_nome=aluno.professor.nome if aluno.professor else None,
        empresa=aluno.empresa,
        invite_code_used=aluno.invite_code_used,
    )


# 🔹 Simulado Livre HFC
@html_bp.route("/aluno/simulado/hfc")
@api_login_required_aluno
def simulado_hfc():
    return render_template("quiz.html", titulo="Simulado Livre - HFC", banco="HFC", questoes=[])


# 🔹 Simulado Livre GPON
@html_bp.route("/aluno/simulado/gpon")
@api_login_required_aluno
def simulado_gpon():
    return render_template("quiz.html", titulo="Simulado Livre - GPON", banco="GPON", questoes=[])


# 🔹 Marcar aluno como pronto na turma
@html_bp.route("/aluno/pronto/<int:turma_id>", methods=["POST"])
@api_login_required_aluno
def aluno_pronto(turma_id):
    aluno_id = session["usuario"]["id"]
    matricula = Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).first()
    if not matricula:
        return jsonify({"success": False, "mensagem": "Matrícula não encontrada para esta turma."}), 404

    turma = Turma.query.get_or_404(turma_id)

    if turma.auto_restart_enabled and turma.status == "Encerrado":
        resetar_ciclo_automatico_turma(turma)

    matricula.pronto = not bool(matricula.pronto)
    mensagem = "Você foi marcado como pronto." if matricula.pronto else "Seu status voltou para aguardando."

    if turma.auto_restart_enabled and matricula.pronto:
        iniciar_turma_se_todos_prontos(turma)

    db.session.commit()

    payload = emitir_atualizacao_turma(turma)

    return jsonify({"success": True, "mensagem": mensagem, **payload})


# 🔹 Rota alternativa para entrar na turma via AJAX (sem redirecionar)
@html_bp.route("/aluno_entrar/<int:turma_id>", methods=["POST"])
@api_login_required_aluno
def aluno_entrar(turma_id):
    aluno_id = session["usuario"]["id"]

    turmas_afetadas = set()
    matriculas_antigas = Matricula.query.filter(
        Matricula.aluno_id == aluno_id,
        Matricula.turma_id != turma_id,
    ).all()

    for matricula_antiga in matriculas_antigas:
        turma_antiga = matricula_antiga.turma
        if turma_antiga and turma_antiga.status != "Encerrado" and not matricula_antiga.respostas:
            turmas_afetadas.add(turma_antiga.id)
            db.session.delete(matricula_antiga)

    matricula = Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).first()
    if not matricula:
        matricula = Matricula(aluno_id=aluno_id, turma_id=turma_id, pronto=False)
        db.session.add(matricula)

    turma = Turma.query.get_or_404(turma_id)
    if turma.auto_restart_enabled and turma.status == "Encerrado":
        resetar_ciclo_automatico_turma(turma)

    db.session.commit()

    for turma_antiga_id in turmas_afetadas:
        turma_antiga = Turma.query.get(turma_antiga_id)
        if turma_antiga:
            emitir_atualizacao_turma(turma_antiga)

    payload = emitir_atualizacao_turma(turma)

    return jsonify({"success": True, **payload})


# 🔹 Sala de espera para o aluno
@html_bp.route("/sala_espera/<int:turma_id>")
@api_login_required_aluno
def sala_espera(turma_id):
    turma = Turma.query.get_or_404(turma_id)

    aluno_id_atual = session["usuario"]["id"]
    alunos = [
        {
            "aluno_id": m.aluno_id,
            "nome": m.aluno.nome,
            "email": m.aluno.email,
            "pronto": m.pronto,
        }
        for m in Matricula.query.filter_by(turma_id=turma.id).all()
    ]

    questoes = [q.to_dict() for q in turma.questoes] if turma.questoes else []
    minha_matricula = Matricula.query.filter_by(aluno_id=aluno_id_atual, turma_id=turma.id).first()

    return render_template(
        "sala_espera.html",
        turma={
            "Turma": turma.id,
            "Nome": turma.nome,
            "Disciplina": turma.disciplina,
            "DATA": turma.data.strftime("%d/%m/%Y") if turma.data else "",
            "Status": turma.status,
            "Sheet": turma.sheet_name,
            "Questoes": questoes,
            "AutoRestartEnabled": bool(getattr(turma, "auto_restart_enabled", False)),
        },
        alunos=alunos,
        aluno_id_atual=aluno_id_atual,
        meu_pronto_inicial=bool(minha_matricula.pronto) if minha_matricula else False,
    )


@html_bp.route("/professor/sala/<int:turma_id>")
@api_login_required_professor
@premium_required
def professor_sala(turma_id):
    turma = Turma.query.filter_by(id=turma_id, professor_id=session["usuario"]["id"]).first_or_404()
    payload = build_turma_realtime_payload(turma)
    questoes_turma = [
        {
            "id": q.id,
            "texto": q.texto,
            "tema": infer_question_theme(q),
            "banco": q.banco,
        }
        for q in sorted(turma.questoes, key=lambda item: item.id)
    ]

    return render_template(
        "professor_sala.html",
        turma=turma,
        questoes_turma=questoes_turma,
        alunos=payload["alunos"],
        total=payload["total"],
        prontos=payload["prontos"],
        status=(payload["status"] or "Aguardando").lower(),
    )


@html_bp.route("/professor/turma/<int:turma_id>/toggle_auto_restart", methods=["POST"])
@api_login_required_professor
@premium_required
def professor_toggle_auto_restart_turma(turma_id):
    turma = Turma.query.filter_by(id=turma_id, professor_id=session["usuario"]["id"]).first_or_404()

    turma.auto_restart_enabled = not bool(turma.auto_restart_enabled)
    if turma.auto_restart_enabled:
        if turma.status == "Encerrado":
            resetar_ciclo_automatico_turma(turma)
        iniciar_turma_se_todos_prontos(turma)
        message = f"Modo automático ativado para a turma {turma.nome}."
    else:
        message = f"Modo automático desativado para a turma {turma.nome}."

    db.session.commit()
    return build_professor_turma_action_response(turma, message, "success")


@html_bp.route("/professor/turma/<int:turma_id>/remover_aluno/<int:aluno_id>", methods=["POST"])
@api_login_required_professor
@premium_required
def professor_remover_aluno_da_sala(turma_id, aluno_id):
    turma = Turma.query.filter_by(id=turma_id, professor_id=session["usuario"]["id"]).first()
    if not turma:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": "Turma não encontrada."}), 404
        flash("Turma não encontrada.", "danger")
        return redirect(url_for("html_bp.professor_dashboard"))

    matricula = Matricula.query.filter_by(turma_id=turma_id, aluno_id=aluno_id).first()
    if not matricula:
        payload = build_turma_realtime_payload(turma)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": "Aluno já não está mais na sala.", **payload}), 404
        flash("Aluno já não está mais na sala.", "warning")
        return redirect(url_for("html_bp.professor_sala", turma_id=turma_id))

    try:
        nome_aluno = matricula.aluno.nome if matricula.aluno else f"ID {aluno_id}"
        for resposta in matricula.respostas:
            resposta.matricula_id = None

        db.session.flush()
        db.session.delete(matricula)
        db.session.commit()

        turma_atualizada = Turma.query.get_or_404(turma_id)
        payload = emitir_atualizacao_turma(turma_atualizada)
        emit_professor_dashboard_update(session["usuario"]["id"])

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "message": f"Aluno {nome_aluno} removido da sala.", **payload})

        flash(f"Aluno {nome_aluno} removido da sala.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao remover aluno da sala pelo professor.")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": "Não foi possível remover o aluno agora."}), 500
        flash("Não foi possível remover o aluno agora.", "danger")

    return redirect(url_for("html_bp.professor_sala", turma_id=turma_id))


# 🔹 Rota para sair da turma
@html_bp.route("/sair_turma/<int:turma_id>", methods=["POST"])
@api_login_required_aluno
def sair_turma(turma_id):
    if "usuario" not in session or session["usuario"]["tipo"] != "aluno":
        return jsonify({"success": False, "mensagem": "Sessão do aluno não encontrada."}), 403

    aluno_id = session["usuario"]["id"]
    turma = Turma.query.get_or_404(turma_id)

    matricula = Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).first()
    if matricula:
        # O aluno realmente sai da turma/sala, mas o histórico de respostas permanece salvo.
        for resposta in matricula.respostas:
            resposta.matricula_id = None

        db.session.flush()
        db.session.delete(matricula)
        db.session.commit()

        payload = emitir_atualizacao_turma(turma)
        return jsonify({
            "success": True,
            "mensagem": "Você saiu da turma. Seu histórico anterior foi preservado e poderá ser visto novamente ao reentrar.",
            **payload,
        })

    return jsonify({"success": True, **build_turma_realtime_payload(turma)})


# 🔹 Histórico detalhado do aluno
@html_bp.route("/aluno/historico")
@api_login_required_aluno
def aluno_historico():
    if "usuario" not in session or session["usuario"]["tipo"] != "aluno":
        return redirect(url_for("html_bp.login_aluno"))

    aluno = Aluno.query.get(session["usuario"]["id"])

    return render_template("aluno_historico.html", cpf=aluno.cpf, historico=[])


# 🔹 Histórico detalhado da turma para o aluno
@html_bp.route("/aluno/historico_turma/<int:turma_id>")
@api_login_required_aluno
def aluno_historico_turma(turma_id):
    if "usuario" not in session or session["usuario"]["tipo"] != "aluno":
        return redirect(url_for("html_bp.login_aluno"))

    aluno = Aluno.query.get(session["usuario"]["id"])

    # Busca todas as respostas do aluno apenas nessa turma
    respostas = Resposta.query.filter_by(aluno_id=aluno.id, turma_id=turma_id).order_by(Resposta.data_envio.desc()).all()

    resultados = []
    for r in respostas:
        questao = Questao.query.get(r.questao_id) if hasattr(r, "questao_id") else None

        resultados.append(
            {
                "data": format_datetime_local(r.data_envio, "%d/%m/%Y %H:%M"),
                "turma_id": r.turma_id,
                "question_text": questao.texto if questao else r.questao,
                "user_option": r.resposta,
                "user_option_text": r.resposta,
                "correct_option": questao.correta if questao else "",
                "correct_option_text": getattr(questao, f"opcao_{questao.correta.lower()}") if questao else "",
                "correct": r.correta,
            }
        )

    return render_template("aluno_historico_turma.html", aluno=aluno, historico=resultados, turma_id=turma_id)


# -----------------------------
# Rota para o aluno excluir uma tentativa (turma ou livre)
@html_bp.route("/aluno/excluir_tentativa", methods=["POST"])
@api_login_required_aluno
def aluno_excluir_tentativa():
    aluno_id = session["usuario"]["id"]
    data = request.get_json() or {}
    turma_id = data.get("turma_id")
    data_iso = data.get("data")

    if not data_iso:
        return jsonify({"erro": "data é obrigatória"}), 400

    try:
        from datetime import timedelta, datetime
        dt = datetime.strptime(data_iso, "%Y-%m-%dT%H:%M:%S")
        dt_end = dt + timedelta(seconds=1)

        query = Resposta.query.filter(
            Resposta.aluno_id == aluno_id,
            Resposta.data_envio >= dt,
            Resposta.data_envio < dt_end,
        )

        if turma_id:
            query = query.filter(Resposta.turma_id == turma_id)

        deletados = query.delete(synchronize_session=False)
        db.session.commit()

        if deletados:
            return jsonify({"mensagem": "Tentativa excluída com sucesso"}), 200
        else:
            return jsonify({"erro": "Nenhuma tentativa encontrada para exclusão"}), 404

    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": f"Falha ao excluir tentativa: {e}"}), 500


# -----------------------------
# Rotas para buscar questões de um banco específico (HTML)
# -----------------------------
@html_bp.route("/get_questoes/<banco>")
@api_login_required_aluno
def get_questoes(banco):
    questoes = Questao.query.filter_by(banco=banco).all()
    if not questoes:
        return jsonify({"erro": "Nenhuma questão encontrada para este banco."}), 404

    import random

    # 🔹 Sorteia até 20 questões
    selecionadas = random.sample(questoes, min(20, len(questoes)))

    data = []
    for q in selecionadas:
        imagem = q.imagem or ""
        if imagem and not (imagem.startswith("http://") or imagem.startswith("https://") or imagem.startswith("/")):
            imagem = url_for("static", filename=imagem)

        data.append(
            {
                "id": q.id,
                "texto": q.texto,
                "opcoes": [
                    {"label": "A", "texto": q.opcao_a},
                    {"label": "B", "texto": q.opcao_b},
                    {"label": "C", "texto": q.opcao_c},
                    {"label": "D", "texto": q.opcao_d},
                ],
                "correta": q.correta,
                "imagem": imagem,
                "banco": q.banco,
            }
        )

    return jsonify(data)


# -----------------------------
# Rotas para buscar questões de um banco específico para o professor (HTML)
@html_bp.route("/get_questoes_professor/<banco>")
@api_login_required_professor
@premium_required
def get_questoes_professor(banco):
    questoes = Questao.query.filter_by(banco=banco).all()
    if not questoes:
        return jsonify({"erro": "Nenhuma questão encontrada para este banco."}), 404

    data = []
    for q in questoes:  # 🔹 professor vê todas, não sorteadas
        imagem = q.imagem or ""
        if imagem and not (imagem.startswith("http://") or imagem.startswith("https://") or imagem.startswith("/")):
            imagem = url_for("static", filename=imagem)

        item = q.to_dict()
        item["imagem"] = imagem
        item["tema"] = infer_question_theme(q)
        data.append(item)

    return jsonify(data)


# -----------------------------
# Rotas de Login (HTML)
# -----------------------------
@html_bp.route("/login/aluno", methods=["GET", "POST"])
def login_aluno():
    if request.method == "POST":
        cpf = normalize_cpf(request.form.get("cpf") or "")
        email = (request.form.get("email") or "").strip().lower()
        senha = request.form.get("senha")

        # Busca por e-mail e valida CPF normalizado para tolerar máscara/formatação.
        aluno = Aluno.query.filter_by(email=email).first()
        cpf_confere = bool(aluno and normalize_cpf(aluno.cpf) == cpf)

        # 🔹 Valida senha
        if cpf_confere and aluno.check_password(senha):
            # Salva dados básicos na sessão
            session["usuario"] = {
                "tipo": "aluno",
                "id": aluno.id,
                "nome": aluno.nome,
                "cpf": aluno.cpf,
                "email": aluno.email,
            }
            approval_message, approval_category = get_aluno_status_message(aluno)
            if approval_message:
                flash(approval_message, approval_category)
            else:
                flash("Login realizado com sucesso!", "success")
            return redirect(url_for("html_bp.aluno_dashboard"))
        else:
            flash("Credenciais inválidas", "danger")
            return redirect(url_for("html_bp.login_aluno"))

    return render_template("login_aluno.html")


@html_bp.route("/login/professor", methods=["GET", "POST"])
def login_professor():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        next_url = request.form.get("next") or request.args.get("next")

        professor = Professor.query.filter_by(email=email).first()
        if professor and professor.check_password(senha):
            if is_admin_email(professor.email) and not professor.is_admin:
                professor.is_admin = True
                professor.is_premium = True
                db.session.commit()

            ensure_professor_invite_code(professor)

            session["usuario"] = {
                "tipo": "professor",
                "id": professor.id,
                "nome": professor.nome,
                "email": professor.email
            }
            flash("Login realizado com sucesso!", "success")
            # segurança: só redirecionar para rotas internas
            if next_url and next_url.startswith("/"):
                return redirect(next_url)
            return redirect(url_for("html_bp.professor_dashboard"))
        else:
            flash("Credenciais inválidas", "danger")
            return redirect(url_for("html_bp.login_professor"))

    # GET
    return render_template("login_professor.html")


# -----------------------------
# Rota de Logout (HTML)
# -----------------------------
@html_bp.route("/professor/logout")
@api_login_required_professor
def professor_logout():
    session.pop("usuario", None)
    flash("Logout realizado com sucesso.", "success")
    return redirect(url_for("html_bp.home"))


@html_bp.route("/professor/convite/regerar", methods=["POST"])
@api_login_required_professor
def professor_regerar_convite():
    professor = Professor.query.get(session["usuario"]["id"])
    if not professor:
        flash("Professor não encontrado.", "danger")
        return redirect(url_for("html_bp.login_professor"))

    professor.invite_code = generate_invite_code()
    db.session.commit()
    emit_professor_dashboard_update(professor.id)
    flash("Novo código de convite gerado com sucesso.", "success")
    return redirect(url_for("html_bp.professor_dashboard"))


@html_bp.route("/professor/alunos/<int:aluno_id>/aprovar", methods=["POST"])
@api_login_required_professor
def professor_aprovar_aluno(aluno_id):
    aluno = Aluno.query.filter_by(id=aluno_id, professor_id=session["usuario"]["id"]).first()
    if not aluno:
        flash("Solicitação de aluno não encontrada.", "danger")
        return redirect(url_for("html_bp.professor_dashboard"))

    aluno.approval_status = "approved"
    aluno.approved_at = datetime.utcnow()
    db.session.commit()
    emit_professor_dashboard_update(session["usuario"]["id"])
    flash(f"Aluno {aluno.nome} aprovado com sucesso.", "success")
    return redirect(url_for("html_bp.professor_dashboard"))


@html_bp.route("/professor/alunos/<int:aluno_id>/recusar", methods=["POST"])
@api_login_required_professor
def professor_recusar_aluno(aluno_id):
    aluno = Aluno.query.filter_by(id=aluno_id, professor_id=session["usuario"]["id"]).first()
    if not aluno:
        flash("Solicitação de aluno não encontrada.", "danger")
        return redirect(url_for("html_bp.professor_dashboard"))

    aluno.approval_status = "rejected"
    aluno.approved_at = None
    db.session.commit()
    emit_professor_dashboard_update(session["usuario"]["id"])
    flash(f"Aluno {aluno.nome} recusado.", "info")
    return redirect(url_for("html_bp.professor_dashboard"))


@html_bp.route("/professor/alunos/<int:aluno_id>/resetar_senha", methods=["POST"])
@api_login_required_professor
def professor_resetar_senha_aluno(aluno_id):
    aluno = Aluno.query.filter_by(id=aluno_id, professor_id=session["usuario"]["id"]).first()
    if not aluno:
        flash("Aluno vinculado não encontrado.", "danger")
        return redirect(url_for("html_bp.professor_dashboard"))

    try:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        temporary_password = "HGP-" + "".join(secrets.choice(alphabet) for _ in range(6))
        aluno.set_password(temporary_password)
        db.session.commit()
        flash(
            f"Senha temporária de {aluno.nome}: {temporary_password}. Oriente o aluno a alterá-la após entrar no sistema.",
            "warning",
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao redefinir senha do aluno pelo professor.")
        flash("Não foi possível redefinir a senha do aluno agora.", "danger")

    return redirect(url_for("html_bp.professor_dashboard"))


@html_bp.route("/professor/alunos/<int:aluno_id>/remover", methods=["POST"])
@api_login_required_professor
def professor_remover_aluno(aluno_id):
    aluno = Aluno.query.filter_by(id=aluno_id, professor_id=session["usuario"]["id"]).first()
    if not aluno:
        flash("Aluno vinculado não encontrado.", "danger")
        return redirect(url_for("html_bp.professor_dashboard"))

    try:
        nome_aluno = aluno.nome
        PasswordReset.query.filter_by(user_type="aluno", user_id=aluno.id).delete(synchronize_session=False)
        Resposta.query.filter_by(aluno_id=aluno.id).delete(synchronize_session=False)
        SimuladoLivre.query.filter_by(aluno_id=aluno.id).delete(synchronize_session=False)
        Matricula.query.filter_by(aluno_id=aluno.id).delete(synchronize_session=False)
        db.session.delete(aluno)
        db.session.commit()
        emit_professor_dashboard_update(session["usuario"]["id"])
        flash(f"Aluno {nome_aluno} removido com sucesso.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao remover aluno vinculado ao professor.")
        flash("Não foi possível remover o aluno agora.", "danger")

    return redirect(url_for("html_bp.professor_dashboard"))


@html_bp.route("/logout")
def logout_page():
    """Página de logout"""
    return render_template("logout.html")


# -----------------------------
# Rotas para iniciar e encerrar quiz (HTML)
# -----------------------------
@html_bp.route("/iniciar_quiz/<int:turma_id>", methods=["POST"])
@api_login_required_professor
@premium_required
def iniciar_quiz(turma_id):
    turma = Turma.query.filter_by(id=turma_id, professor_id=session["usuario"]["id"]).first_or_404()
    turma.status = "Em andamento"
    db.session.commit()
    return build_professor_turma_action_response(turma, "Quiz iniciado com sucesso!", "success")


@html_bp.route("/start_quiz")
@api_login_required_aluno
def start_quiz():
    sheet = request.args.get("sheet")
    turma_id = request.args.get("turma")

    # 🔹 Carregar questões aleatórias do banco escolhido
    questoes = Questao.query.filter_by(banco=sheet).order_by(db.func.random()).limit(10).all()

    return make_no_cache_response(render_template(
        "quiz.html",
        titulo=f"Quiz Aleatório - {sheet}",
        banco=sheet,
        turma_id=turma_id,
        questoes=[q.to_dict() for q in questoes],
    ))


@html_bp.route("/start_quiz_manual")
def start_quiz_manual():
    turma_id = request.args.get("turma")
    turma = Turma.query.get_or_404(turma_id)

    # 🔹 Carregar questões vinculadas à turma
    questoes = turma.questoes

    return make_no_cache_response(render_template(
        "quiz.html",
        titulo=f"Quiz Manual - {turma.nome}",
        banco=turma.sheet_name,
        turma_id=turma.id,
        questoes=[q.to_dict() for q in questoes],
    ))


@html_bp.route("/encerrar_quiz/<int:turma_id>", methods=["POST"])
@api_login_required_professor
@premium_required
def encerrar_quiz(turma_id):
    turma = Turma.query.filter_by(id=turma_id, professor_id=session["usuario"]["id"]).first_or_404()

    turma.status = "Encerrado"

    for matricula in turma.matriculas:
        matricula.pronto = False

    db.session.commit()

    return build_professor_turma_action_response(
        turma,
        "Quiz encerrado, respostas registradas e turma marcada como encerrada.",
        "info",
    )


@html_bp.route("/resetar_turma/<int:turma_id>", methods=["POST"])
@api_login_required_professor
@premium_required
def resetar_turma(turma_id):
    turma = Turma.query.filter_by(id=turma_id, professor_id=session["usuario"]["id"]).first_or_404()

    turma.status = "Aguardando"

    for matricula in turma.matriculas:
        matricula.pronto = False

    db.session.commit()

    return build_professor_turma_action_response(turma, "Turma reiniciada e pronta para novo ciclo.", "info")


# -----------------------------
# Rota para o quiz do aluno (HTML)
# -----------------------------
@html_bp.route("/aluno/quiz/<int:turma_id>")
def aluno_quiz(turma_id):
    # Ajuste: verifique sessão do usuário
    if "usuario" not in session or session["usuario"]["tipo"] != "aluno":
        return redirect(url_for("html_bp.login_aluno"))

    turma = Turma.query.get_or_404(turma_id)

    # Verifica se o aluno está matriculado e pronto
    aluno_id = session["usuario"]["id"]
    matricula = Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma.id).first()
    if not matricula or not matricula.pronto:
        return redirect(url_for("html_bp.aluno_dashboard"))

    # Busca as questões vinculadas à turma
    questoes_turma = QuestaoTurma.query.filter_by(turma_id=turma.id).all()
    questoes = [Questao.query.get(qt.questao_id) for qt in questoes_turma]

    return make_no_cache_response(render_template("quiz.html", questoes=questoes, titulo=f"Quiz da Turma {turma.nome}"))


# -----------------------------
# Rota para mostrar resultados individuais do professor (HTML)
# -----------------------------
@html_bp.route("/professor/resultados_individuais")
@api_login_required_professor
@premium_required
def resultados_individuais():
    alunos = Aluno.query.all()
    dados_alunos = []
    scores = []

    for aluno in alunos:
        respostas = (
            Resposta.query.filter(
                Resposta.aluno_id == aluno.id,
                Resposta.turma_id.is_(None),
            )
            .order_by(Resposta.data_envio.desc())
            .all()
        )
        if not respostas:
            continue

        simulados = {}
        for r in respostas:
            chave = f"{aluno.id}-{r.data_envio.strftime('%Y%m%d%H%M%S')}"
            if chave not in simulados:
                simulados[chave] = {
                    "nome": aluno.nome,
                    "cpf": getattr(aluno, "cpf", ""),
                    "email": aluno.email,
                    "total_correct": 0,
                    "total_questions": 0,
                    "score": 0,
                    "data": format_datetime_local(r.data_envio, "%d/%m/%Y %H:%M:%S"),
                    "data_iso": r.data_envio.strftime("%Y-%m-%dT%H:%M:%S"),
                    "sheet_name": f"Simulado Livre - {r.banco}" if r.banco else "Simulado Livre",
                    "results": [],
                }

            questao = r.questao
            simulados[chave]["results"].append(
                {
                    "question_text": questao.texto,
                    "correct": r.correta,
                    "correct_option": questao.correta,
                    "correct_option_text": getattr(questao, f"opcao_{questao.correta.strip().lower()}", None),
                    "user_option": r.resposta,
                    "user_option_text": getattr(questao, f"opcao_{r.resposta.strip().lower()}", None) if r.resposta else None,
                }
            )
            simulados[chave]["total_questions"] += 1
            if r.correta:
                simulados[chave]["total_correct"] += 1

        for s in simulados.values():
            s["score"] = round((s["total_correct"] / s["total_questions"]) * 10, 1) if s["total_questions"] > 0 else 0
            dados_alunos.append(s)
            scores.append(s["score"])

    dados_alunos.sort(key=lambda item: item.get("data_iso", ""), reverse=True)
    media = round(mean(scores), 2) if scores else 0
    melhor = max(scores) if scores else 0
    pior = min(scores) if scores else 0

    return make_no_cache_response(
        render_template("resultados_individuais.html", alunos=dados_alunos, media=media, melhor=melhor, pior=pior)
    )


# -----------------------------
# Rota para mostrar resultados da turma (HTML)
# -----------------------------
@html_bp.route("/turma_result/<int:turma_id>")
@api_login_required_professor
@premium_required
def turma_result(turma_id):
    turma = Turma.query.get_or_404(turma_id)

    alunos_data = []
    notas = []
    respostas = (
        Resposta.query.filter_by(turma_id=turma.id)
        .order_by(Resposta.data_envio.desc())
        .all()
    )

    simulados = {}
    for r in respostas:
        aluno = r.aluno
        if not aluno:
            continue

        chave = f"{aluno.id}-{r.data_envio.strftime('%Y%m%d%H%M%S')}"
        if chave not in simulados:
            simulados[chave] = {
                "nome": aluno.nome,
                "cpf": getattr(aluno, "cpf", ""),
                "email": aluno.email,
                "score": 0,
                "total_correct": 0,
                "total_questions": 0,
                "data": format_datetime_local(r.data_envio, "%d/%m/%Y %H:%M:%S"),
                "data_iso": r.data_envio.strftime("%Y-%m-%dT%H:%M:%S"),
                "sheet_name": turma.sheet_name or turma.disciplina,
                "results": [],
            }

        questao = r.questao
        if not questao:
            continue

        simulados[chave]["results"].append(
            {
                "question_text": questao.texto,
                "correct": r.correta,
                "correct_option": questao.correta,
                "correct_option_text": getattr(questao, f"opcao_{questao.correta.lower()}", None),
                "user_option": r.resposta,
                "user_option_text": getattr(questao, f"opcao_{r.resposta.lower()}", None) if r.resposta else None,
            }
        )
        simulados[chave]["total_questions"] += 1
        if r.correta:
            simulados[chave]["total_correct"] += 1

    for s in simulados.values():
        s["score"] = round((s["total_correct"] / s["total_questions"]) * 10, 1) if s["total_questions"] > 0 else 0
        alunos_data.append(s)
        notas.append(s["score"])

    alunos_data.sort(key=lambda item: item.get("data_iso", ""), reverse=True)
    media = round(mean(notas), 2) if notas else 0
    melhor = max(notas) if notas else 0
    pior = min(notas) if notas else 0

    return make_no_cache_response(
        render_template("turma_result.html", turma_id=turma.id, alunos=alunos_data, media=media, melhor=melhor, pior=pior)
    )


# -----------------------------
# Rota para excluir uma turma (HTML)
# -----------------------------
@html_bp.route("/delete_turma/<int:turma_id>", methods=["POST"])
@api_login_required_professor
def delete_turma(turma_id):
    turma = Turma.query.get_or_404(turma_id)
    try:
        db.session.delete(turma)
        db.session.commit()

        # Se veio via fetch (AJAX), retorna JSON
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "message": f"Turma '{turma.nome}' excluída com sucesso!"})

        # Fluxo tradicional (formulário)
        flash(f"Turma '{turma.nome}' excluída com sucesso!", "success")
        return redirect(url_for("html_bp.professor_dashboard"))

    except Exception as e:
        db.session.rollback()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": str(e)}), 500

        flash(f"Erro ao excluir turma: {e}", "danger")
        return redirect(url_for("html_bp.professor_dashboard"))


# -----------------------------
# Rota para enviar respostas do quiz (HTML)
# -----------------------------
@html_bp.route("/submit_answers/<int:turma_id>", methods=["POST"])
def submit_answers(turma_id):
    if "usuario" not in session or session["usuario"]["tipo"] != "aluno":
        return redirect(url_for("html_bp.login_aluno"))

    aluno_id = session["usuario"]["id"]
    data = request.get_json(silent=True) or {}
    answers = data.get("answers", {}) if isinstance(data, dict) else {}

    if not isinstance(answers, dict) or not answers:
        return jsonify({"success": False, "message": "Nenhuma resposta foi recebida para correção."}), 400

    matricula = Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).first()
    if not matricula:
        flash("Você não está matriculado nesta turma.", "danger")
        return redirect(url_for("html_bp.aluno_dashboard"))

    attempt_time = datetime.utcnow().replace(microsecond=0)

    question_ids = []
    for questao_id in answers.keys():
        try:
            question_ids.append(int(questao_id))
        except (TypeError, ValueError):
            continue

    questoes = Questao.query.filter(Questao.id.in_(question_ids)).all() if question_ids else []
    questoes_por_id = {questao.id: questao for questao in questoes}

    resultados = []
    novas_respostas = []
    total_correct = 0

    for questao_id, resposta_dada in answers.items():
        try:
            questao = questoes_por_id.get(int(questao_id))
        except (TypeError, ValueError):
            questao = None

        if not questao:
            continue

        resposta_normalizada = str(resposta_dada or "").strip().upper()[:1]
        correta_normalizada = str(questao.correta or "").strip().upper()[:1]
        correta = bool(resposta_normalizada and correta_normalizada and resposta_normalizada == correta_normalizada)

        if correta:
            total_correct += 1

        novas_respostas.append(
            Resposta(
                aluno_id=aluno_id,
                turma_id=turma_id,
                matricula_id=matricula.id,
                questao_id=questao.id,
                resposta=resposta_normalizada,
                correta=correta,
                banco=None,
                tipo="turma",
                data_envio=attempt_time,
            )
        )

        resultados.append(
            {
                "question_text": questao.texto,
                "user_option": resposta_normalizada,
                "user_option_text": getattr(questao, f"opcao_{resposta_normalizada.lower()}", None) if resposta_normalizada else None,
                "correct_option": correta_normalizada,
                "correct_option_text": getattr(questao, f"opcao_{correta_normalizada.lower()}", None) if correta_normalizada else None,
                "correct": correta,
            }
        )

    if not novas_respostas:
        return jsonify({"success": False, "message": "Nenhuma resposta válida foi processada."}), 400

    db.session.add_all(novas_respostas)

    # 🔹 Marca o aluno como pronto
    matricula.pronto = True
    db.session.commit()

    total = len(resultados)
    total_wrong = max(total - total_correct, 0)

    # Nota proporcional para qualquer quantidade de questões
    nota = round((total_correct / total) * 10, 1) if total > 0 else 0.0

    aluno = Aluno.query.get(aluno_id)

    # 🔹 Verifica se todos os alunos finalizaram
    turma = Turma.query.get(turma_id)
    matriculas = Matricula.query.filter_by(turma_id=turma_id).all()
    todos_finalizaram = all(m.pronto for m in matriculas)

    if todos_finalizaram:
        if turma.auto_restart_enabled:
            resetar_ciclo_automatico_turma(turma)
        else:
            turma.status = "Encerrado"

    db.session.commit()

    # 🔹 Atualiza sala/painel em tempo real com status e contadores
    emitir_atualizacao_turma(turma)

    # 🔹 Renderiza resultado final do quiz
    return make_no_cache_response(render_template(
        "quiz_result.html",
        aluno=aluno,
        total=total,
        total_correct=total_correct,
        total_wrong=total_wrong,
        nota=nota,
        results=resultados,
        turma=turma,
    ))


# -----------------------------
# Rota para voltar para o painel do aluno ou sala de espera (HTML)
# -----------------------------
@html_bp.route("/voltar_painel", methods=["GET"])
def voltar_painel():
    if "usuario" not in session or session["usuario"]["tipo"] != "aluno":
        return redirect(url_for("html_bp.login_aluno"))

    aluno_id = session["usuario"]["id"]

    # Primeiro tenta a matrícula mais recente.
    matricula = Matricula.query.filter_by(aluno_id=aluno_id).order_by(Matricula.id.desc()).first()
    if matricula and matricula.turma:
        return redirect(url_for("html_bp.sala_espera", turma_id=matricula.turma_id))

    # Fallback: se a matrícula antiga foi removida, usa a última turma com histórico salvo.
    ultima_resposta_turma = (
        Resposta.query.filter(
            Resposta.aluno_id == aluno_id,
            Resposta.turma_id.isnot(None),
        )
        .order_by(Resposta.data_envio.desc())
        .first()
    )
    if ultima_resposta_turma and ultima_resposta_turma.turma_id:
        return redirect(url_for("html_bp.sala_espera", turma_id=ultima_resposta_turma.turma_id))

    return redirect(url_for("html_bp.aluno_dashboard"))


# -----------------------------
# Rota para enviar respostas do simulado livre (HTML)
# -----------------------------
@html_bp.route("/submit_answers_free", methods=["POST"])
def submit_answers_free():
    if "usuario" not in session or session["usuario"]["tipo"] != "aluno":
        return redirect(url_for("html_bp.login_aluno"))

    aluno_id = session["usuario"]["id"]
    data = request.get_json(silent=True) or {}
    answers = data.get("answers", {}) if isinstance(data, dict) else {}
    banco = data.get("banco") if isinstance(data, dict) else None  # "HFC" ou "GPON"

    if not isinstance(answers, dict) or not answers:
        return jsonify({"success": False, "message": "Nenhuma resposta foi recebida para correção."}), 400

    attempt_time = datetime.utcnow().replace(microsecond=0)

    question_ids = []
    for questao_id in answers.keys():
        try:
            question_ids.append(int(questao_id))
        except (TypeError, ValueError):
            continue

    questoes = Questao.query.filter(Questao.id.in_(question_ids)).all() if question_ids else []
    questoes_por_id = {questao.id: questao for questao in questoes}

    resultados = []
    novas_respostas = []
    total_correct = 0

    for questao_id, resposta_dada in answers.items():
        try:
            questao = questoes_por_id.get(int(questao_id))
        except (TypeError, ValueError):
            questao = None

        if not questao:
            continue

        resposta_normalizada = str(resposta_dada or "").strip().upper()[:1]
        correta_normalizada = str(questao.correta or "").strip().upper()[:1]
        correta = bool(resposta_normalizada and correta_normalizada and resposta_normalizada == correta_normalizada)
        if correta:
            total_correct += 1

        novas_respostas.append(
            Resposta(
                aluno_id=aluno_id,
                turma_id=None,
                matricula_id=None,
                questao_id=questao.id,
                resposta=resposta_normalizada,
                correta=correta,
                banco=banco,
                tipo="livre",
                data_envio=attempt_time,
            )
        )

        resultados.append(
            {
                "question_text": questao.texto,
                "user_option": resposta_normalizada,
                "user_option_text": getattr(questao, f"opcao_{resposta_normalizada.lower()}", None) if resposta_normalizada else None,
                "correct_option": correta_normalizada,
                "correct_option_text": getattr(questao, f"opcao_{correta_normalizada.lower()}", None) if correta_normalizada else None,
                "correct": correta,
            }
        )

    if not novas_respostas:
        return jsonify({"success": False, "message": "Nenhuma resposta válida foi processada."}), 400

    db.session.add_all(novas_respostas)

    total = len(resultados)
    total_wrong = max(total - total_correct, 0)

    # 🔹 Cálculo da nota de 0 a 10 para qualquer quantidade de questões
    nota = round((total_correct / total) * 10, 1) if total > 0 else 0

    valor_por_questao = 0.5
    pontuacao_total = total * valor_por_questao
    pontuacao_obtida = total_correct * valor_por_questao
    pontuacao_percentual = (pontuacao_obtida / pontuacao_total) * 100 if total > 0 else 0

    novo_simulado = SimuladoLivre(
        aluno_id=aluno_id,
        banco=banco,
        pontuacao=pontuacao_obtida,
        acertos=total_correct,
        data_realizacao=attempt_time,
    )
    db.session.add(novo_simulado)

    db.session.commit()

    aluno = Aluno.query.get(aluno_id)

    return make_no_cache_response(render_template(
        "quiz_result.html",
        aluno=aluno,
        score=pontuacao_obtida,
        total=total,
        total_correct=total_correct,
        total_wrong=total_wrong,
        pontuacao_total=pontuacao_total,
        pontuacao_percentual=pontuacao_percentual,
        results=resultados,
        banco=banco,
        nota=nota,
    ))


# -----------------------------
# Rota para mostrar resultados do quiz individual (HTML)
# -----------------------------
@html_bp.route("/quiz_result/<int:turma_id>/<int:aluno_id>")
def quiz_result(turma_id, aluno_id):
    aluno = Aluno.query.get_or_404(aluno_id)
    respostas = Resposta.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).order_by(Resposta.data_envio.asc()).all()

    total = len(respostas)
    total_correct = sum(1 for r in respostas if r.correta)
    total_wrong = max(total - total_correct, 0)
    nota = round((total_correct / total) * 10, 1) if total > 0 else 0

    results = []
    for r in respostas:
        questao = r.questao
        resposta_normalizada = str(r.resposta or "").strip().upper()[:1]
        correta_normalizada = str(getattr(questao, "correta", "") or "").strip().upper()[:1]
        results.append(
            {
                "question_text": questao.texto if questao else "",
                "user_option": resposta_normalizada,
                "user_option_text": getattr(questao, f"opcao_{resposta_normalizada.lower()}", None) if questao and resposta_normalizada else None,
                "correct_option": correta_normalizada,
                "correct_option_text": getattr(questao, f"opcao_{correta_normalizada.lower()}", None) if questao and correta_normalizada else None,
                "correct": r.correta,
            }
        )

    # 🔹 Busca a turma (se existir)
    turma = Turma.query.get(turma_id)

    return render_template(
        "quiz_result.html",
        aluno=aluno,
        total=total,
        total_correct=total_correct,
        total_wrong=total_wrong,
        nota=nota,
        results=results,
        turma=turma,
    )


# -----------------------------
# Excluir todos os historicos de alunos/resultados
# -----------------------------
@html_bp.route("/delete_all", methods=["POST"])
@api_login_required_professor
def delete_all():
    try:
        # Remove todos os resultados (mas mantém alunos e turmas)
        Resposta.query.delete()
        SimuladoLivre.query.delete()
        db.session.commit()
        flash("Todas as respostas e simulados foram excluídos com sucesso!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao excluir registros: {e}", "danger")
    return redirect(url_for("html_bp.resultados_individuais"))


# -----------------------------
# Rota para excluir respostas de uma tentativa específica (HTML)
# -----------------------------
@html_bp.route("/delete_resposta/<cpf>/<data_iso>", methods=["POST"])
@api_login_required_professor
def delete_resposta(cpf, data_iso):
    try:
        aluno = Aluno.query.filter_by(cpf=cpf).first()
        if not aluno:
            flash("Aluno não encontrado.", "warning")
            return redirect(url_for("html_bp.resultados_individuais"))

        # Converter string ISO para datetime (minuto ou segundo)
        try:
            data_dt = datetime.strptime(data_iso, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            data_dt = datetime.strptime(data_iso, "%Y-%m-%dT%H:%M")

        data_inicio = data_dt
        data_fim = data_dt + timedelta(seconds=1)

        turma_id = request.args.get("turma_id", type=int)

        query = Resposta.query.filter(Resposta.aluno_id == aluno.id, Resposta.data_envio >= data_inicio, Resposta.data_envio <= data_fim)
        if turma_id:
            query = query.filter(Resposta.turma_id == turma_id)

        deletados = query.delete(synchronize_session=False)

        # Apagar simulado livre dessa tentativa se houver
        query_simulado = SimuladoLivre.query.filter(SimuladoLivre.aluno_id == aluno.id,
                                                    SimuladoLivre.data_realizacao >= data_inicio,
                                                    SimuladoLivre.data_realizacao <= data_fim)
        if turma_id:
            # simulado livre não está vinculado a turma, então ignoramos
            pass

        query_simulado.delete(synchronize_session=False)

        db.session.commit()

        flash(f"Tentativa de {aluno.nome} em {format_datetime_local(data_dt, '%d/%m/%Y %H:%M:%S')} excluída com sucesso! ({deletados} respostas)", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao excluir respostas: {e}", "danger")
    return redirect(url_for("html_bp.resultados_individuais"))


# -----------------------------
# Excluir todas as respostas de um aluno em uma turma
@html_bp.route("/delete_respostas_aluno_turma/<int:turma_id>/<cpf>", methods=["POST"])
@api_login_required_professor
def delete_respostas_aluno_turma(turma_id, cpf):
    try:
        aluno = Aluno.query.filter_by(cpf=cpf).first()
        if not aluno:
            flash("Aluno não encontrado.", "warning")
            return redirect(url_for("html_bp.turma_result", turma_id=turma_id))

        deletados = Resposta.query.filter_by(aluno_id=aluno.id, turma_id=turma_id).delete(synchronize_session=False)
        db.session.commit()

        if deletados:
            flash(f"{deletados} respostas do aluno {aluno.nome} na turma foram excluídas com sucesso.", "success")
        else:
            flash("Nenhuma resposta encontrada para exclusão.", "warning")

    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao excluir respostas da turma: {e}", "danger")

    return redirect(url_for("html_bp.turma_result", turma_id=turma_id))


# -----------------------------
# Excluir múltiplas tentativas livres (bulk) - sem turma
@html_bp.route("/bulk_delete_respostas_livres", methods=["POST"])
@api_login_required_professor
def bulk_delete_respostas_livres():
    data = request.get_json(silent=True) or {}
    tentativas = data.get("tentativas", [])
    if not isinstance(tentativas, list):
        return jsonify({"ok": False, "error": "Payload inválido: campo 'tentativas' deve ser uma lista."}), 400

    alunos_por_cpf = {}
    for a in Aluno.query.all():
        cpf_norm = normalize_cpf(getattr(a, "cpf", ""))
        if cpf_norm:
            alunos_por_cpf[cpf_norm] = a

    total = 0
    try:
        for t in tentativas:
            if not isinstance(t, dict):
                continue

            cpf = normalize_cpf(t.get("cpf", ""))
            data_iso = (t.get("data_iso", "") or "").strip()
            if not cpf or not data_iso:
                continue

            aluno = alunos_por_cpf.get(cpf)
            if not aluno:
                continue

            data_dt = parse_attempt_datetime(data_iso)
            if not data_dt:
                continue

            data_fim = data_dt + timedelta(seconds=1)
            deleted = Resposta.query.filter(
                Resposta.aluno_id == aluno.id,
                Resposta.data_envio >= data_dt,
                Resposta.data_envio <= data_fim,
            ).delete(synchronize_session=False)
            SimuladoLivre.query.filter(
                SimuladoLivre.aluno_id == aluno.id,
                SimuladoLivre.data_realizacao >= data_dt,
                SimuladoLivre.data_realizacao <= data_fim,
            ).delete(synchronize_session=False)
            total += deleted
        db.session.commit()
        return jsonify({"ok": True, "deleted": total})
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro no bulk_delete_respostas_livres")
        msg = str(e).strip() or e.__class__.__name__
        return jsonify({"ok": False, "error": msg}), 500


# -----------------------------
# Excluir múltiplas tentativas (bulk) de uma turma
@html_bp.route("/bulk_delete_tentativas/<int:turma_id>", methods=["POST"])
@api_login_required_professor
def bulk_delete_tentativas(turma_id):
    data = request.get_json(silent=True) or {}
    tentativas = data.get("tentativas", [])
    if not isinstance(tentativas, list):
        return jsonify({"ok": False, "error": "Payload inválido: campo 'tentativas' deve ser uma lista."}), 400

    alunos_por_cpf = {}
    for a in Aluno.query.all():
        cpf_norm = normalize_cpf(getattr(a, "cpf", ""))
        if cpf_norm:
            alunos_por_cpf[cpf_norm] = a

    total = 0
    try:
        for t in tentativas:
            if not isinstance(t, dict):
                continue

            cpf = normalize_cpf(t.get("cpf", ""))
            data_iso = (t.get("data_iso", "") or "").strip()
            if not cpf or not data_iso:
                continue

            aluno = alunos_por_cpf.get(cpf)
            if not aluno:
                continue

            data_dt = parse_attempt_datetime(data_iso)
            if not data_dt:
                continue

            data_fim = data_dt + timedelta(seconds=1)
            deleted = Resposta.query.filter(
                Resposta.aluno_id == aluno.id,
                Resposta.turma_id == turma_id,
                Resposta.data_envio >= data_dt,
                Resposta.data_envio <= data_fim,
            ).delete(synchronize_session=False)
            total += deleted
        db.session.commit()
        return jsonify({"ok": True, "deleted": total})
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erro no bulk_delete_tentativas")
        msg = str(e).strip() or e.__class__.__name__
        return jsonify({"ok": False, "error": msg}), 500


# -----------------------------
# Esqueci a senha - Aluno (token-based)
# -----------------------------
@html_bp.route("/forgot_password/aluno", methods=["GET", "POST"])
def forgot_password_aluno():
    if request.method == "POST":
        try:
            cpf = normalize_cpf(request.form.get("cpf", ""))
            email = request.form.get("email", "").strip().lower()
            if not email:
                flash("Preencha o e-mail.", "warning")
                return redirect(url_for("html_bp.forgot_password_aluno"))

            aluno = Aluno.query.filter_by(email=email).first()
            if aluno and cpf and normalize_cpf(aluno.cpf) != cpf:
                aluno = None

            if not aluno:
                flash("Aluno não encontrado com os dados informados.", "danger")
                return redirect(url_for("html_bp.forgot_password_aluno"))

            sent = create_password_reset(user_type="aluno", user_id=aluno.id, email=aluno.email, nome=aluno.nome)
            if sent:
                flash("Um e-mail com instruções para redefinir a senha foi enviado (verifique spam).", "success")
            else:
                flash("Não foi possível enviar o e-mail de recuperação agora. Verifique a configuração SMTP do sistema e tente novamente.", "danger")
            return redirect(url_for("html_bp.login_aluno"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Erro ao processar recuperação de senha do aluno.")
            flash("Erro interno ao processar recuperação de senha. Tente novamente em instantes.", "danger")
            return redirect(url_for("html_bp.forgot_password_aluno"))

    return render_template("forgot_password_aluno.html")


# -----------------------------
# Esqueci a senha - Professor (token-based)
# -----------------------------
@html_bp.route("/forgot_password/professor", methods=["GET", "POST"])
def forgot_password_professor():
    if request.method == "POST":
        try:
            email = request.form.get("email", "").strip().lower()
            if not email:
                flash("Preencha o e-mail.", "warning")
                return redirect(url_for("html_bp.forgot_password_professor"))

            professor = Professor.query.filter_by(email=email).first()
            if not professor:
                flash("Professor não encontrado com esse e-mail.", "danger")
                return redirect(url_for("html_bp.forgot_password_professor"))

            sent = create_password_reset(user_type="professor", user_id=professor.id, email=professor.email, nome=professor.nome)
            if sent:
                flash("Um e-mail com instruções para redefinir a senha foi enviado (verifique spam).", "success")
            else:
                flash("Não foi possível enviar o e-mail de recuperação agora. Verifique a configuração SMTP do sistema e tente novamente.", "danger")
            return redirect(url_for("html_bp.login_professor"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Erro ao processar recuperação de senha do professor.")
            flash("Erro interno ao processar recuperação de senha. Tente novamente em instantes.", "danger")
            return redirect(url_for("html_bp.forgot_password_professor"))

    return render_template("forgot_password_professor.html")


# -----------------------------
# Reset de senha (link enviado por e-mail)
# URL: /reset_password?token=<token>
# -----------------------------
@html_bp.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    token = request.args.get("token") if request.method == "GET" else request.form.get("token")
    if not token:
        flash("Token inválido.", "danger")
        return redirect(url_for("html_bp.home"))

    pr = validate_token(token)
    if not pr:
        flash("Token inválido ou expirado.", "danger")
        return redirect(url_for("html_bp.home"))

    # Se for POST, processa nova senha
    if request.method == "POST":
        senha = request.form.get("senha")
        senha_confirm = request.form.get("senha_confirm")
        if not senha or not senha_confirm:
            flash("Preencha ambos os campos de senha.", "warning")
            return redirect(url_for("html_bp.reset_password", token=token))
        if senha != senha_confirm:
            flash("As senhas não conferem.", "warning")
            return redirect(url_for("html_bp.reset_password", token=token))
        if len(senha) < 6:
            flash("Senha muito curta. Use ao menos 6 caracteres.", "warning")
            return redirect(url_for("html_bp.reset_password", token=token))

        # aplica nova senha no usuário correspondente
        if pr.user_type == "aluno":
            user = Aluno.query.get(pr.user_id)
        else:
            user = Professor.query.get(pr.user_id)

        if not user:
            flash("Usuário não encontrado.", "danger")
            return redirect(url_for("html_bp.home"))

        user.set_password(senha)
        pr.used = True
        db.session.commit()

        flash("Senha alterada com sucesso. Faça login com a nova senha.", "success")
        if pr.user_type == "aluno":
            return redirect(url_for("html_bp.login_aluno"))
        else:
            return redirect(url_for("html_bp.login_professor"))

    # GET -> mostra formulário
    return render_template("reset_password.html", token=token)


# -----------------------------
# Rotas Premium para Professores
# -----------------------------
@html_bp.route("/professor/premium")
@api_login_required_professor
def professor_premium():
    from payment_utils import generate_pix_qrcode, get_pix_display_data
    from mercadopago_gateway import MercadoPagoGateway
    
    professor = Professor.query.get(session["usuario"]["id"])
    is_active = professor.is_premium_active() if professor else False
    expires_at = professor.premium_expires_at.strftime("%d/%m/%Y") if professor and professor.premium_expires_at else None
    subscription_status = "Free"
    if professor:
        if professor.is_admin:
            subscription_status = "VIP Vitalício"
            expires_at = "Vitalício"
        elif is_active:
            subscription_status = "Premium"

    # Exibição padrão do PIX (sem criar cobrança pendente automaticamente)
    qr_code, pix_code = generate_pix_qrcode(250.00)
    pix_data = get_pix_display_data()

    pix_payment_id = None
    pix_ticket_url = None
    mercadopago_error_message = None
    pending_payment = None

    if professor:
        pending_payment = Payment.query.filter(
            Payment.professor_id == professor.id,
            Payment.method.in_(["pix", "mercadopago", "stripe_pix"]),
            Payment.status.in_(["pending", "processing", "in_process"])
        ).order_by(Payment.created_at.desc()).first()

        pending_mp_payment = Payment.query.filter(
            Payment.professor_id == professor.id,
            Payment.method == "mercadopago",
            Payment.status.in_(["pending", "processing", "in_process"]),
            Payment.external_ref.isnot(None)
        ).order_by(Payment.created_at.desc()).first()

        if pending_mp_payment and MercadoPagoGateway.is_configured():
            external_ref = (pending_mp_payment.external_ref or "").strip()
            if external_ref.isdigit():
                mp_result = MercadoPagoGateway.obter_pagamento(external_ref)
                if mp_result.get("success"):
                    mp_info = mp_result.get("payment", {})
                    mp_status = (mp_info.get("status") or "").lower()
                    transaction_data = ((mp_info.get("point_of_interaction") or {}).get("transaction_data") or {})

                    pix_payment_id = mp_info.get("id")
                    pix_ticket_url = transaction_data.get("ticket_url")
                    if transaction_data.get("qr_code"):
                        pix_code = transaction_data.get("qr_code")
                    if transaction_data.get("qr_code_base64"):
                        qr_code = f"data:image/png;base64,{transaction_data.get('qr_code_base64')}"

                    if mp_status == "approved" and pending_mp_payment.status != "completed":
                        pending_mp_payment.status = "completed"
                        pending_mp_payment.updated_at = datetime.utcnow()

                        if not professor.is_admin:
                            if professor.premium_expires_at and professor.premium_expires_at > datetime.utcnow():
                                professor.premium_expires_at = professor.premium_expires_at + timedelta(days=30)
                            else:
                                professor.premium_expires_at = datetime.utcnow() + timedelta(days=30)
                            professor.is_premium = True

                        db.session.commit()
                        is_active = professor.is_premium_active()
                        subscription_status = "VIP Vitalício" if professor.is_admin else "Premium"
                        expires_at = "Vitalício" if professor.is_admin else (
                            professor.premium_expires_at.strftime("%d/%m/%Y") if professor.premium_expires_at else None
                        )
                        pending_payment = None
                else:
                    mercadopago_error_message = mp_result.get("error")
        elif not MercadoPagoGateway.is_configured():
            mercadopago_error_message = (
                "Mercado Pago não está configurado. Defina MERCADOPAGO_ACCESS_TOKEN."
            )

    return render_template(
        "professor_premium.html",
        is_active=is_active,
        expires_at=expires_at,
        subscription_status=subscription_status,
        qr_code=qr_code,
        pix_code=pix_code,
        pix_data=pix_data,
        pix_payment_id=pix_payment_id,
        pix_ticket_url=pix_ticket_url,
        mercadopago_error_message=mercadopago_error_message,
        pending_payment=pending_payment,
    )


@html_bp.route("/professor/premium/sucesso_mp")
@api_login_required_professor
def professor_premium_sucesso_mp():
    """Página de sucesso após pagamento Mercado Pago."""
    professor = Professor.query.get(session["usuario"]["id"])
    if not professor:
        flash("Professor não encontrado.", "danger")
        return redirect(url_for("html_bp.login_professor"))

    recent_payment = Payment.query.filter_by(
        professor_id=professor.id,
        method="mercadopago",
        status="completed"
    ).order_by(Payment.updated_at.desc()).first()

    if recent_payment:
        flash("🎉 Pagamento com cartão confirmado com sucesso! Sua assinatura premium foi ativada automaticamente.", "success")
    else:
        flash("Pagamento com cartão em processamento. A confirmação pode levar alguns minutos.", "info")

    return redirect(url_for("html_bp.professor_premium"))


@html_bp.route("/professor/premium/sucesso_pix")
@api_login_required_professor
def professor_premium_sucesso_pix():
    """Página de sucesso após pagamento PIX via Stripe."""
    professor = Professor.query.get(session["usuario"]["id"])
    if not professor:
        flash("Professor não encontrado.", "danger")
        return redirect(url_for("html_bp.login_professor"))

    # Verificar se há pagamentos recentes confirmados
    recent_payment = Payment.query.filter_by(
        professor_id=professor.id,
        method="stripe_pix",
        status="completed"
    ).order_by(Payment.completed_at.desc()).first()

    if recent_payment:
        flash("🎉 Pagamento PIX confirmado com sucesso! Sua assinatura premium foi liberada após conferência.", "success")
    else:
        flash("Pagamento PIX em processamento. A liberação ocorrerá após conferência manual.", "info")

    return redirect(url_for("html_bp.professor_premium"))


@html_bp.route("/professor/pagar_pix", methods=["POST"])
@api_login_required_professor
def professor_pagar_pix():
    """Registrar pagamento via PIX para conferência manual."""
    professor = Professor.query.get(session["usuario"]["id"])
    if not professor:
        flash("Professor não encontrado.", "danger")
        return redirect(url_for("html_bp.professor_premium"))

    from datetime import datetime

    pix_code = request.form.get("pix_code")
    payment = Payment(
        professor_id=professor.id,
        method="pix",
        amount=250.00,
        status="pending",
        description="Pagamento PIX para assinatura premium",
        external_ref=pix_code,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.session.add(payment)
    db.session.commit()

    flash(
        "✅ Pagamento via PIX enviado para conferência manual. O premium será liberado após a validação.",
        "info"
    )
    return redirect(url_for("html_bp.professor_premium"))


@html_bp.route("/webhook/mercadopago", methods=["POST"])
def mercadopago_webhook():
    """Webhook para notificações de pagamento do Mercado Pago."""
    from mercadopago_gateway import MercadoPagoGateway

    payload = request.get_json(silent=True) or {}
    payment_id = None

    if isinstance(payload, dict) and payload.get("data"):
        payment_id = payload["data"].get("id")

    if not payment_id:
        payment_id = request.args.get("id") or payload.get("id")

    if not payment_id:
        return jsonify({"error": "payment_id não encontrado"}), 400

    result = MercadoPagoGateway.obter_pagamento(payment_id)
    if not result.get("success"):
        return jsonify({"error": result.get("error")}), 400

    payment_info = result.get("payment", {})
    if payment_info.get("status") == "approved":
        external_reference = payment_info.get("external_reference")
        try:
            payment = Payment.query.get(int(external_reference)) if external_reference and external_reference.isdigit() else None
        except Exception:
            payment = None

        if payment and payment.status != "completed":
            payment.status = "completed"
            payment.updated_at = datetime.utcnow()

            professor = payment.professor
            if professor:
                if professor.premium_expires_at and professor.premium_expires_at > datetime.utcnow():
                    professor.premium_expires_at = professor.premium_expires_at + timedelta(days=30)
                else:
                    professor.premium_expires_at = datetime.utcnow() + timedelta(days=30)
                professor.is_premium = True

            db.session.commit()

    return jsonify({"status": "success"}), 200


@html_bp.route("/professor/pagar_cartao", methods=["POST"])
@api_login_required_professor
def professor_pagar_cartao():
    """Gera um novo checkout do Mercado Pago para pagamento com cartão a cada clique."""
    from mercadopago_gateway import MercadoPagoGateway

    professor = Professor.query.get(session["usuario"]["id"])
    if not professor:
        flash("Professor não encontrado.", "danger")
        return redirect(url_for("html_bp.professor_premium"))

    if not MercadoPagoGateway.is_configured():
        flash("Mercado Pago não está configurado. Defina MERCADOPAGO_ACCESS_TOKEN.", "danger")
        return redirect(url_for("html_bp.professor_premium"))

    try:
        mp_payment = Payment(
            professor_id=professor.id,
            method="mercadopago",
            amount=250.00,
            status="pending",
            description="Checkout Mercado Pago para assinatura premium",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.session.add(mp_payment)
        db.session.commit()

        checkout_url, checkout_reference = MercadoPagoGateway.criar_preferencia(
            professor.id,
            professor.email,
            250.00,
            mp_payment.id,
            base_url=request.host_url,
        )

        if not checkout_url:
            db.session.delete(mp_payment)
            db.session.commit()
            flash(f"Não foi possível gerar um novo checkout agora. {checkout_reference}", "danger")
            return redirect(url_for("html_bp.professor_premium"))

        mp_payment.external_ref = str(checkout_reference)
        db.session.commit()
        return redirect(checkout_url)
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao gerar novo checkout Mercado Pago: {e}", "danger")
        return redirect(url_for("html_bp.professor_premium"))


@html_bp.route("/professor/assinar_premium", methods=["POST"])
@api_login_required_professor
def professor_assinar_premium():
    professor = Professor.query.get(session["usuario"]["id"])
    if not professor:
        flash("Professor não encontrado.", "danger")
        return redirect(url_for("html_bp.professor_premium"))

    # Simulação de pagamento: em produção, integrar com gateway de pagamento
    # Aqui, assumimos que o pagamento foi aprovado
    from datetime import datetime, timedelta

    # Ativa premium por 30 dias
    if professor.premium_expires_at and professor.premium_expires_at > datetime.utcnow():
        # Renova a partir da data atual de expiração
        professor.premium_expires_at = professor.premium_expires_at + timedelta(days=30)
    else:
        # Ativa a partir de agora
        professor.premium_expires_at = datetime.utcnow() + timedelta(days=30)

    professor.is_premium = True
    db.session.commit()

    flash("Assinatura premium ativada com sucesso! Válida até {}. Aproveite todas as funcionalidades.".format(professor.premium_expires_at.strftime("%d/%m/%Y")), "success")
    return redirect(url_for("html_bp.professor_dashboard"))


@html_bp.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    """
    Webhook para processar eventos do Stripe (pagamentos confirmados)
    """
    import stripe
    from stripe_gateway import StripeGateway
    
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('stripe-signature')
    
    # Verificar webhook secret (deve ser configurado no Stripe Dashboard)
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    if not endpoint_secret:
        current_app.logger.error("STRIPE_WEBHOOK_SECRET não configurado")
        return jsonify({"error": "Webhook secret not configured"}), 500
    
    try:
        # Verificar assinatura do webhook
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except ValueError as e:
        # Payload inválido
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.error.SignatureVerificationError as e:
        # Assinatura inválida
        return jsonify({"error": "Invalid signature"}), 400
    
    # Processar evento
    if event['type'] == 'charge.succeeded':
        charge = event['data']['object']
        
        # Extrair metadados
        professor_id = charge.get('metadata', {}).get('professor_id')
        tipo_servico = charge.get('metadata', {}).get('tipo_servico')
        
        if professor_id and tipo_servico == 'premium_subscription':
            try:
                professor = Professor.query.get(int(professor_id))
                if professor:
                    # Ativar premium por 30 dias
                    if professor.premium_expires_at and professor.premium_expires_at > datetime.utcnow():
                        professor.premium_expires_at = professor.premium_expires_at + timedelta(days=30)
                    else:
                        professor.premium_expires_at = datetime.utcnow() + timedelta(days=30)
                    
                    professor.is_premium = True
                    db.session.commit()
                    
                    current_app.logger.info(f"Premium ativado via webhook para professor {professor_id}")
                else:
                    current_app.logger.error(f"Professor {professor_id} não encontrado no webhook")
            except Exception as e:
                current_app.logger.error(f"Erro ao processar webhook: {str(e)}")
                return jsonify({"error": "Processing error"}), 500
    
    return jsonify({"status": "success"}), 200


# -----------------------------
# Aplicar premium_required nas rotas protegidas
# -----------------------------