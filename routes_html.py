# routes_html.py
# -----------------------------
# Rotas HTML (templates)
# -----------------------------
import os
import secrets
import hashlib
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime, timedelta

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
    Em dev, se SMTP_HOST não estiver definido, o e-mail será impresso no console.
    """
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_from = os.getenv("SMTP_FROM", smtp_user or "no-reply@example.com")
    use_ssl = os.getenv("SMTP_USE_SSL", "").lower() in ("1", "true", "yes") or smtp_port == 465

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_address
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    if not smtp_host:
        # Fallback para desenvolvimento: apenas logar no console
        print("=== send_email (DEV fallback) ===")
        print("To:", to_address)
        print("Subject:", subject)
        print("Body (text):")
        print(body_text)
        if body_html:
            print("Body (html):")
            print(body_html)
        print("=== end ===")
        return True

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
                server.starttls(context=context)
                if smtp_user and smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        return True
    except Exception as e:
        # Em produção, use logger
        print("Erro ao enviar e-mail:", e)
        return False


def create_password_reset(user_type: str, user_id: int, email: str, nome: str) -> bool:
    """
    Gera token, salva hash no banco e envia e-mail com link de reset.
    user_type: 'aluno' ou 'professor'
    """
    token = generate_token()
    token_hash = hash_token(token)

    # salva no banco
    pr = PasswordReset(token_hash=token_hash, user_type=user_type, user_id=user_id)
    db.session.add(pr)
    db.session.commit()

    reset_url = url_for("html_bp.reset_password", token=token, _external=True)

    subject = "Redefinição de senha - Simulador ETN"
    body_text = f"""Olá {nome},

Você solicitou a redefinição de senha. Acesse o link abaixo para escolher uma nova senha. O link expira em {RESET_TOKEN_EXPIRY//60} minutos.

{reset_url}

Se você não solicitou, ignore esta mensagem.

Atenciosamente,
Equipe Simulador ETN
"""
    body_html = f"""
<p>Olá {nome},</p>
<p>Você solicitou a redefinição de senha. Acesse o link abaixo para escolher uma nova senha. O link expira em <strong>{RESET_TOKEN_EXPIRY//60} minutos</strong>.</p>
<p><a href="{reset_url}">Redefinir minha senha</a></p>
<p>Se você não solicitou, ignore esta mensagem.</p>
<p>Atenciosamente,<br>Equipe Simulador ETN</p>
"""
    sent = send_email(email, subject, body_text, body_html)
    return sent


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


# -----------------------------
# Funções utilitárias de atualização em tempo real
# -----------------------------
def build_turma_realtime_payload(turma):
    alunos_data = [{"nome": m.aluno.nome, "pronto": bool(m.pronto)} for m in turma.matriculas]
    return {
        "id": turma.id,
        "status": turma.status,
        "prontos": sum(1 for m in turma.matriculas if m.pronto),
        "total": len(turma.matriculas),
        "alunos": alunos_data,
    }


def emitir_atualizacao_turma(turma):
    payload = build_turma_realtime_payload(turma)
    socketio.emit("status_turma_atualizado", payload)
    socketio.emit("alunos_prontos_atualizado", payload)
    return payload


def atualizar_status_turma(turma_id, novo_status):
    turma = Turma.query.get(turma_id)
    if turma:
        turma.status = novo_status
        db.session.commit()
        emitir_atualizacao_turma(turma)


@html_bp.route("/")
def home():
    """Página inicial"""
    return render_template("home.html")


@html_bp.route("/home")
def home_alias():
    return redirect(url_for("html_bp.home"))


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


@html_bp.route("/professor/register", methods=["GET", "POST"])
def professor_register():
    if request.method == "POST":
        nome = request.form.get("username")  # campo do formulário
        email = request.form.get("email")  # se você quiser pedir email também
        senha = request.form.get("password")

        # Verifica se já existe professor com esse email
        existente = Professor.query.filter_by(email=email).first()
        if existente:
            return render_template("professor_register.html", message="Email já cadastrado!")

        # Cria novo professor sem acesso premium
        novo_professor = Professor(nome=nome, email=email)
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
        nome = request.form.get("nome")
        cpf = request.form.get("cpf")
        email = request.form.get("email")
        senha = request.form.get("senha")

        # Verifica se já existe aluno com mesmo email ou CPF
        if Aluno.query.filter((Aluno.email == email) | (Aluno.cpf == cpf)).first():
            flash("Já existe um aluno com este CPF ou e-mail.", "danger")
            return redirect(url_for("html_bp.aluno_register"))

        aluno = Aluno(nome=nome, cpf=cpf, email=email)
        aluno.set_password(senha)
        db.session.add(aluno)
        db.session.commit()

        flash("Cadastro realizado com sucesso! Faça login.", "success")
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
    subscription_status = "Free"
    expires_at = None
    if professor:
        if professor.is_admin:
            subscription_status = "VIP Vitalício"
            expires_at = "Vitalício"
        elif professor.is_premium_active():
            subscription_status = "Premium"
            expires_at = professor.premium_expires_at.strftime("%d/%m/%Y") if professor.premium_expires_at else None

    return render_template(
        "professor_dashboard.html",
        nome=professor_nome,
        turmas=turmas,
        subscription_status=subscription_status,
        expires_at=expires_at,
    )


@html_bp.route("/aluno/dashboard", methods=["GET", "POST"])
def aluno_dashboard():
    # ✅ Verifica se o aluno está logado
    if "usuario" not in session or session["usuario"]["tipo"] != "aluno":
        return redirect(url_for("html_bp.login_aluno"))

    aluno = Aluno.query.get(session["usuario"]["id"])

    # ✅ Entrar em turma
    if request.method == "POST":
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
    turmas = [m.turma for m in aluno.matriculas]

    # ✅ Histórico detalhado
    respostas = Resposta.query.filter_by(aluno_id=aluno.id).order_by(Resposta.data_envio.desc()).all()
    historico = []
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

    return render_template("aluno_dashboard.html", nome=aluno.nome, turmas=turmas, historico=historico)


# 🔹 Simulado Livre HFC
@html_bp.route("/aluno/simulado/hfc")
def simulado_hfc():
    return render_template("quiz.html", titulo="Simulado Livre - HFC", banco="HFC", questoes=[])


# 🔹 Simulado Livre GPON
@html_bp.route("/aluno/simulado/gpon")
def simulado_gpon():
    return render_template("quiz.html", titulo="Simulado Livre - GPON", banco="GPON", questoes=[])


# 🔹 Marcar aluno como pronto na turma
@html_bp.route("/aluno/pronto/<int:turma_id>", methods=["POST"])
@api_login_required_aluno
def aluno_pronto(turma_id):
    aluno_id = session["usuario"]["id"]
    matricula = Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).first()
    if not matricula:
        return jsonify({"status": "erro", "mensagem": "Matrícula não encontrada para esta turma."}), 404

    matricula.pronto = True
    db.session.commit()

    turma = Turma.query.get_or_404(turma_id)
    payload = emitir_atualizacao_turma(turma)

    return jsonify({"status": "ok", **payload})


# 🔹 Rota alternativa para entrar na turma via AJAX (sem redirecionar)
@html_bp.route("/aluno_entrar/<int:turma_id>", methods=["POST"])
@api_login_required_aluno
def aluno_entrar(turma_id):
    aluno_id = session["usuario"]["id"]

    matricula = Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).first()
    if not matricula:
        matricula = Matricula(aluno_id=aluno_id, turma_id=turma_id, pronto=False)
        db.session.add(matricula)
        db.session.commit()

    turma = Turma.query.get_or_404(turma_id)
    payload = emitir_atualizacao_turma(turma)

    return jsonify({"status": "ok", **payload})


# 🔹 Sala de espera para o aluno
@html_bp.route("/sala_espera/<int:turma_id>")
@api_login_required_aluno
def sala_espera(turma_id):
    turma = Turma.query.get_or_404(turma_id)

    alunos = [{"nome": m.aluno.nome, "pronto": m.pronto} for m in Matricula.query.filter_by(turma_id=turma.id).all()]

    questoes = [q.to_dict() for q in turma.questoes] if turma.questoes else []

    return render_template(
        "sala_espera.html",
        turma={
            "Turma": turma.id,
            "Disciplina": turma.disciplina,
            "DATA": turma.data.strftime("%d/%m/%Y %H:%M"),
            "Status": turma.status,
            "Sheet": turma.sheet_name,
            "Questoes": questoes,
        },
        alunos=alunos,
    )


# 🔹 Rota para sair da turma
@html_bp.route("/sair_turma/<int:turma_id>", methods=["POST"])
@api_login_required_aluno
def sair_turma(turma_id):
    if "usuario" not in session or session["usuario"]["tipo"] != "aluno":
        return jsonify({"status": "unauthorized"}), 403

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
            "status": "ok",
            "mensagem": "Você saiu da turma. Seu histórico anterior foi preservado.",
            **payload,
        })

    return jsonify({"status": "ok", **build_turma_realtime_payload(turma)})


# 🔹 Histórico detalhado do aluno
@html_bp.route("/aluno/historico")
def aluno_historico():
    if "usuario" not in session or session["usuario"]["tipo"] != "aluno":
        return redirect(url_for("html_bp.login_aluno"))

    aluno = Aluno.query.get(session["usuario"]["id"])

    return render_template("aluno_historico.html", cpf=aluno.cpf, historico=[])


# 🔹 Histórico detalhado da turma para o aluno
@html_bp.route("/aluno/historico_turma/<int:turma_id>")
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
                "data": r.data_envio.strftime("%d/%m/%Y %H:%M"),
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
        data.append(item)

    return jsonify(data)


# -----------------------------
# Rotas de Login (HTML)
# -----------------------------
@html_bp.route("/login/aluno", methods=["GET", "POST"])
def login_aluno():
    if request.method == "POST":
        cpf = request.form.get("cpf")
        email = request.form.get("email")
        senha = request.form.get("senha")

        # 🔹 Busca o aluno pelo CPF e email
        aluno = Aluno.query.filter_by(email=email, cpf=cpf).first()

        # 🔹 Valida senha
        if aluno and aluno.check_password(senha):
            # Salva dados básicos na sessão
            session["usuario"] = {
                "tipo": "aluno",
                "id": aluno.id,
                "nome": aluno.nome,
                "cpf": aluno.cpf,
                "email": aluno.email,
            }
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
    turma = Turma.query.get_or_404(turma_id)
    turma.status = "Em andamento"
    db.session.commit()
    atualizar_status_turma(turma.id, turma.status)
    flash("Quiz iniciado com sucesso!", "success")
    return redirect(url_for("html_bp.professor_dashboard"))


@html_bp.route("/start_quiz")
def start_quiz():
    sheet = request.args.get("sheet")
    turma_id = request.args.get("turma")

    # 🔹 Carregar questões aleatórias do banco escolhido
    questoes = Questao.query.filter_by(banco=sheet).order_by(db.func.random()).limit(10).all()

    return render_template(
        "quiz.html",
        titulo=f"Quiz Aleatório - {sheet}",
        banco=sheet,
        turma_id=turma_id,
        questoes=[q.to_dict() for q in questoes],
    )


@html_bp.route("/start_quiz_manual")
def start_quiz_manual():
    turma_id = request.args.get("turma")
    turma = Turma.query.get_or_404(turma_id)

    # 🔹 Carregar questões vinculadas à turma
    questoes = turma.questoes

    return render_template(
        "quiz.html",
        titulo=f"Quiz Manual - {turma.nome}",
        banco=turma.sheet_name,
        turma_id=turma.id,
        questoes=[q.to_dict() for q in questoes],
    )


@html_bp.route("/encerrar_quiz/<int:turma_id>", methods=["POST"])
@api_login_required_professor
@premium_required
def encerrar_quiz(turma_id):
    turma = Turma.query.get_or_404(turma_id)

    # 🔹 2. Atualizar status da turma
    turma.status = "Encerrado"

    # 🔹 3. Resetar flag "pronto" dos alunos
    for matricula in turma.matriculas:
        matricula.pronto = False

    db.session.commit()

    # 🔹 4. Emitir evento em tempo real
    atualizar_status_turma(turma.id, turma.status)

    flash("Quiz encerrado, respostas registradas e turma marcada como encerrada.", "info")
    return redirect(url_for("html_bp.professor_dashboard"))


@html_bp.route("/resetar_turma/<int:turma_id>", methods=["POST"])
@api_login_required_professor
@premium_required
def resetar_turma(turma_id):
    turma = Turma.query.get_or_404(turma_id)

    # 🔹 1. Resetar status da turma
    turma.status = "Aguardando"

    # 🔹 2. Resetar flag "pronto" de todos os alunos
    for matricula in turma.matriculas:
        matricula.pronto = False

    # 🔹 3. Persistir alterações
    db.session.commit()

    # 🔹 4. Emitir evento em tempo real
    atualizar_status_turma(turma.id, turma.status)

    flash("Turma reiniciada e pronta para novo ciclo.", "info")
    return redirect(url_for("html_bp.professor_dashboard"))


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

    return render_template("quiz.html", questoes=questoes, titulo=f"Quiz da Turma {turma.nome}")


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
                    "data": r.data_envio.strftime("%d/%m/%Y %H:%M:%S"),
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

    return render_template("resultados_individuais.html", alunos=dados_alunos, media=media, melhor=melhor, pior=pior)


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
                "data": r.data_envio.strftime("%d/%m/%Y %H:%M:%S"),
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

    return render_template("turma_result.html", turma_id=turma.id, alunos=alunos_data, media=media, melhor=melhor, pior=pior)


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
    data = request.get_json()
    answers = data.get("answers", {})

    matricula = Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).first()
    if not matricula:
        flash("Você não está matriculado nesta turma.", "danger")
        return redirect(url_for("html_bp.aluno_dashboard"))

    attempt_time = datetime.utcnow().replace(microsecond=0)

    resultados = []
    for questao_id, resposta_dada in answers.items():
        questao = Questao.query.get(int(questao_id))
        if not questao:
            continue

        correta = (resposta_dada.lower() == questao.correta.lower())

        nova_resposta = Resposta(
            aluno_id=aluno_id,
            turma_id=turma_id,
            matricula_id=matricula.id,
            questao_id=questao.id,
            resposta=resposta_dada,
            correta=correta,
            banco=None,
            tipo="turma",
            data_envio=attempt_time,
        )
        db.session.add(nova_resposta)

        resultados.append(
            {
                "question_text": questao.texto,
                "user_option": resposta_dada,
                "user_option_text": getattr(questao, f"opcao_{resposta_dada.lower()}"),
                "correct_option": questao.correta,
                "correct_option_text": getattr(questao, f"opcao_{questao.correta.lower()}"),
                "correct": correta,
            }
        )

    # 🔹 Marca o aluno como pronto
    matricula.pronto = True
    db.session.commit()

    total = len(resultados)
    total_correct = sum(1 for r in resultados if r["correct"])

    # Nota proporcional (ex: 9 acertos em 20 = 4.5)
    nota = (total_correct / total * 10) if total > 0 else 0.0

    aluno = Aluno.query.get(aluno_id)

    # 🔹 Verifica se todos os alunos finalizaram
    turma = Turma.query.get(turma_id)
    matriculas = Matricula.query.filter_by(turma_id=turma_id).all()
    todos_finalizaram = all(m.pronto for m in matriculas)

    if todos_finalizaram:
        turma.status = "Encerrado"
        db.session.commit()

    # 🔹 Atualiza sala/painel em tempo real com status e contadores
    emitir_atualizacao_turma(turma)

    # 🔹 Renderiza resultado final do quiz
    return render_template("quiz_result.html", aluno=aluno, total=total, total_correct=total_correct, nota=nota, results=resultados, turma=turma)


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
    data = request.get_json()
    answers = data.get("answers", {})
    banco = data.get("banco")  # "HFC" ou "GPON"

    attempt_time = datetime.utcnow().replace(microsecond=0)

    resultados = []
    total_correct = 0

    for questao_id, resposta_dada in answers.items():
        questao = Questao.query.get(int(questao_id))
        if not questao:
            continue

        correta = (resposta_dada == questao.correta)
        if correta:
            total_correct += 1

        nova_resposta = Resposta(
            aluno_id=aluno_id,
            turma_id=None,
            matricula_id=None,
            questao_id=questao.id,
            resposta=resposta_dada,
            correta=correta,
            banco=banco,
            tipo="livre",  # 🔹 marca como simulado livre
            data_envio=attempt_time,
        )
        db.session.add(nova_resposta)

        resultados.append(
            {
                "question_text": questao.texto,
                "user_option": resposta_dada,
                "user_option_text": getattr(questao, f"opcao_{resposta_dada.lower()}"),
                "correct_option": questao.correta,
                "correct_option_text": getattr(questao, f"opcao_{questao.correta.lower()}"),
                "correct": correta,
            }
        )

    total = len(resultados)

    # 🔹 Cálculo da nota de 0 a 10
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

    return render_template(
        "quiz_result.html",
        aluno=aluno,
        score=pontuacao_obtida,
        total=total,
        total_correct=total_correct,
        pontuacao_total=pontuacao_total,
        pontuacao_percentual=pontuacao_percentual,
        results=resultados,
        banco=banco,
        nota=nota,  # 🔹 agora o template recebe a nota
    )


# -----------------------------
# Rota para mostrar resultados do quiz individual (HTML)
# -----------------------------
@html_bp.route("/quiz_result/<int:turma_id>/<int:aluno_id>")
def quiz_result(turma_id, aluno_id):
    aluno = Aluno.query.get_or_404(aluno_id)
    respostas = Resposta.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).all()

    total = len(respostas)
    total_correct = sum(1 for r in respostas if r.correta)
    score = total_correct  # ou outra lógica de pontuação

    results = []
    for r in respostas:
        results.append(
            {
                "question_text": r.questao,
                "user_option": r.resposta,
                "user_option_text": r.resposta,  # se tiver texto da opção
                "correct_option": "✔" if r.correta else "✘",
                "correct_option_text": r.questao,  # ou texto correto da questão
                "correct": r.correta,
            }
        )

    # 🔹 Busca a turma (se existir)
    turma = Turma.query.get(turma_id)

    return render_template("quiz_result.html", aluno=aluno, score=score, total=total, total_correct=total_correct, results=results, turma=turma)


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

        flash(f"Tentativa de {aluno.nome} em {data_dt.strftime('%d/%m/%Y %H:%M:%S')} excluída com sucesso! ({deletados} respostas)", "success")
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
# Esqueci a senha - Aluno (token-based)
# -----------------------------
@html_bp.route("/forgot_password/aluno", methods=["GET", "POST"])
def forgot_password_aluno():
    if request.method == "POST":
        cpf = request.form.get("cpf", "").strip()
        email = request.form.get("email", "").strip().lower()
        if not cpf or not email:
            flash("Preencha CPF e e-mail.", "warning")
            return redirect(url_for("html_bp.forgot_password_aluno"))

        aluno = Aluno.query.filter_by(email=email, cpf=cpf).first()
        if not aluno:
            flash("Aluno não encontrado com esse CPF/e-mail.", "danger")
            return redirect(url_for("html_bp.forgot_password_aluno"))

        sent = create_password_reset(user_type="aluno", user_id=aluno.id, email=aluno.email, nome=aluno.nome)
        if sent:
            flash("Um e-mail com instruções para redefinir a senha foi enviado (verifique spam).", "success")
        else:
            flash("Erro ao enviar e-mail. Tente novamente mais tarde.", "danger")
        return redirect(url_for("html_bp.login_aluno"))

    return render_template("forgot_password_aluno.html")


# -----------------------------
# Esqueci a senha - Professor (token-based)
# -----------------------------
@html_bp.route("/forgot_password/professor", methods=["GET", "POST"])
def forgot_password_professor():
    if request.method == "POST":
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
            flash("Erro ao enviar e-mail. Tente novamente mais tarde.", "danger")
        return redirect(url_for("html_bp.login_professor"))

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
    
    # Gerar PIX QR code (fallback)
    qr_code, pix_code = generate_pix_qrcode(250.00)
    pix_data = get_pix_display_data()

    # Tentar criar PIX direto no Mercado Pago + checkout para cartão
    pix_payment_url = None
    pix_payment_id = None
    pix_ticket_url = None
    mercadopago_error_message = None
    try:
        if professor:
            if MercadoPagoGateway.is_configured():
                mp_payment = Payment(
                    professor_id=professor.id,
                    method="mercadopago",
                    amount=250.00,
                    status="pending",
                    description="Pagamento Mercado Pago para assinatura premium",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.session.add(mp_payment)
                db.session.commit()

                pix_result = MercadoPagoGateway.criar_pagamento_pix(
                    professor.email,
                    250.00,
                    mp_payment.id,
                    professor.nome,
                )
                if pix_result.get("success"):
                    pix_payment_id = pix_result.get("payment_id")
                    pix_ticket_url = pix_result.get("ticket_url")
                    if pix_result.get("qr_code"):
                        pix_code = pix_result.get("qr_code")
                    if pix_result.get("qr_code_base64"):
                        qr_code = f"data:image/png;base64,{pix_result.get('qr_code_base64')}"
                    mp_payment.external_ref = str(pix_payment_id)
                    db.session.commit()
                else:
                    mercadopago_error_message = pix_result.get("error")

                # Mantém checkout externo apenas para cartão
                pix_payment_url, checkout_reference = MercadoPagoGateway.criar_preferencia(
                    professor.id, professor.email, 250.00, mp_payment.id
                )
                if not pix_payment_url and not mercadopago_error_message:
                    mercadopago_error_message = checkout_reference
            else:
                mercadopago_error_message = (
                    "Mercado Pago não está configurado. Defina MERCADOPAGO_ACCESS_TOKEN."
                )
    except Exception as e:
        mercadopago_error_message = f"Erro ao criar pagamento Mercado Pago: {e}"
        print(mercadopago_error_message)
        if 'mp_payment' in locals():
            db.session.delete(mp_payment)
            db.session.commit()

    pending_payment = None
    if professor:
        pending_payment = Payment.query.filter(
            Payment.professor_id == professor.id,
            Payment.method.in_(["pix", "mercadopago"]),
            Payment.status == "pending"
        ).order_by(Payment.created_at.desc()).first()

    return render_template(
        "professor_premium.html",
        is_active=is_active,
        expires_at=expires_at,
        subscription_status=subscription_status,
        qr_code=qr_code,
        pix_code=pix_code,
        pix_data=pix_data,
        pix_payment_url=pix_payment_url,
        pix_payment_id=pix_payment_id,
        pix_ticket_url=pix_ticket_url,
        mercadopago_error_message=mercadopago_error_message,
        pending_payment=pending_payment
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
        flash("🎉 Pagamento Mercado Pago confirmado com sucesso! Sua assinatura premium foi ativada.", "success")
    else:
        flash("Pagamento em processamento. A confirmação pode levar alguns minutos.", "info")

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
        flash("🎉 Pagamento PIX confirmado com sucesso! Sua assinatura premium foi ativada.", "success")
    else:
        flash("Pagamento em processamento. A confirmação pode levar alguns minutos.", "info")

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
        "✅ Pagamento via PIX notificado. A assinatura será registrada como pendente e será confirmada manualmente.",
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
    """
    Processa pagamento real via Stripe.
    O token é gerado seguramente pelo Stripe.js no frontend.
    """
    from stripe_gateway import StripeGateway
    
    professor = Professor.query.get(session["usuario"]["id"])
    if not professor:
        flash("Professor não encontrado.", "danger")
        return redirect(url_for("html_bp.professor_premium"))
    
    # Receber token do Stripe.js
    stripe_token = request.form.get("stripeToken")
    if not stripe_token:
        flash("Erro: Token de pagamento não fornecido.", "danger")
        return redirect(url_for("html_bp.professor_premium"))
    
    # Processar pagamento com Stripe
    result = StripeGateway.processar_token_cartao(
        stripe_token,
        professor.email,
        professor.id,
        valor=250.00
    )
    
    if result["success"] and result["paid"]:
        # Pagamento confirmado - ativar premium
        if professor.premium_expires_at and professor.premium_expires_at > datetime.utcnow():
            professor.premium_expires_at = professor.premium_expires_at + timedelta(days=30)
        else:
            professor.premium_expires_at = datetime.utcnow() + timedelta(days=30)
        
        professor.is_premium = True
        db.session.commit()
        
        flash("✅ Pagamento realizado com sucesso! Assinatura premium ativada por 30 dias.", "success")
        return redirect(url_for("html_bp.professor_dashboard"))
    else:
        flash(f"❌ Erro no pagamento: {result['error']}", "danger")
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