import random
from flask import Blueprint, request, jsonify, session, redirect, url_for, flash
from models import db, Professor, Turma, Aluno, Matricula, Questao, Resposta, SimuladoLivre, Payment
from functools import wraps
from datetime import datetime, timedelta

try:
    import stripe
except ImportError:
    stripe = None

# Helper to detect if request likely expects JSON (API/AJAX) or HTML (browser navigation)
def prefers_json():
    return (
        request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or (request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html)
    )

def api_login_required_aluno(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "usuario" not in session or session["usuario"]["tipo"] != "aluno":
            if prefers_json():
                return jsonify({"erro": "Acesso restrito a alunos"}), 403
            # redirect to aluno login (HTML flow), keep next to return later
            flash("Faça login para acessar a área do aluno.", "warning")
            return redirect(url_for("html_bp.login_aluno", next=request.path))

        aluno = Aluno.query.get(session["usuario"]["id"])
        if aluno and not aluno.is_approved():
            status = (aluno.approval_status or "pending").lower()
            message = (
                "Seu cadastro está pendente de aprovação do professor."
                if status == "pending"
                else "Seu cadastro foi recusado pelo professor responsável."
            )
            if prefers_json():
                return jsonify({"erro": message, "status": status}), 403
            flash(message, "warning" if status == "pending" else "danger")
            return redirect(url_for("html_bp.aluno_dashboard"))

        return f(*args, **kwargs)
    return decorated_function

def api_login_required_professor(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "usuario" not in session or session["usuario"]["tipo"] != "professor":
            if prefers_json():
                return jsonify({"erro": "Acesso restrito a professores"}), 403
            # redirect to professor login (HTML flow), keep next to return later
            flash("Faça login para acessar o painel do professor.", "warning")
            return redirect(url_for("html_bp.login_professor", next=request.path))
        return f(*args, **kwargs)
    return decorated_function

def api_premium_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "usuario" not in session or session["usuario"]["tipo"] != "professor":
            if prefers_json():
                return jsonify({"erro": "Acesso negado"}), 403
            flash("Acesso negado.", "danger")
            return redirect(url_for("html_bp.login_professor"))
        professor = Professor.query.get(session["usuario"]["id"])
        if not professor or not professor.is_premium_active():
            if prefers_json():
                return jsonify({"erro": "Esta funcionalidade requer assinatura premium. Assine agora por R$ 250/mês."}), 403
            flash("Esta funcionalidade requer assinatura premium. Assine agora por R$ 250/mês.", "warning")
            return redirect(url_for("html_bp.professor_premium"))
        return f(*args, **kwargs)
    return decorated_function

# Helper function to build results from respostas
def build_results_from_respostas(respostas):
    results = []
    total_questions = 0
    total_correct = 0
    for r in respostas:
        questao = Questao.query.get(r.questao_id)
        result = {
            "question_text": questao.texto if questao else "",
            "user_option": r.resposta,
            "user_option_text": getattr(questao, f"opcao_{r.resposta.strip().lower()}", "") if questao and r.resposta else "",
            "correct_option": questao.correta if questao else "",
            "correct_option_text": getattr(questao, f"opcao_{questao.correta.strip().lower()}", "") if questao and questao.correta else "",
            "correct": r.correta,
        }
        results.append(result)
        total_questions += 1
        if r.correta:
            total_correct += 1
    return results, total_questions, total_correct

# Criando o Blueprint para API
api_bp = Blueprint("api_bp", __name__)

# -----------------------------
# Painel do Professor (API)
# -----------------------------
@api_bp.route("/api/professor_dashboard")
@api_login_required_professor
@api_premium_required
def api_professor_dashboard():
    professor_id = session["usuario"]["id"]
    turmas = Turma.query.filter_by(professor_id=professor_id).all()

    data = []
    for t in turmas:
        alunos_count = len(t.matriculas)
        prontos_count = sum(1 for m in t.matriculas if getattr(m, "pronto", False))

        data.append({
            "id": t.id,
            "nome": t.nome,
            "disciplina": t.disciplina,
            "data": t.data.isoformat() if t.data else "",
            "sheet_name": t.sheet_name,
            "status": t.status,
            "alunos": alunos_count,
            "prontos": prontos_count
        })

    return jsonify({"turmas": data})

# -----------------------------
# Questões (API)
# -----------------------------
@api_bp.route("/api/questoes/<banco>", methods=["GET"])
def questoes_por_banco(banco):
    questoes = Questao.query.filter_by(banco=banco).all()
    selecionadas = random.sample(questoes, min(20, len(questoes)))
    return jsonify([{
        "id": q.id,
        "texto": q.texto,
        "opcao_a": q.opcao_a,
        "opcao_b": q.opcao_b,
        "opcao_c": q.opcao_c,
        "opcao_d": q.opcao_d,
        "correta": q.correta,
        "imagem": q.imagem,
        "banco": q.banco
    } for q in selecionadas])

@api_bp.route("/api/questoes/<banco>", methods=["POST"])
def questoes_escolhidas(banco):
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", [])[:20]
    questoes = Questao.query.filter(Questao.banco == banco, Questao.id.in_(ids)).all()
    return jsonify([{
        "id": q.id,
        "texto": q.texto,
        "opcao_a": q.opcao_a,
        "opcao_b": q.opcao_b,
        "opcao_c": q.opcao_c,
        "opcao_d": q.opcao_d,
        "correta": q.correta,
        "imagem": q.imagem,
        "banco": q.banco
    } for q in questoes])

# -----------------------------
# Login de Professor (API)
# -----------------------------
@api_bp.route("/api/login/professor", methods=["POST"])
def api_login_professor():
    data = request.get_json() or {}
    email = data.get("email")
    senha = data.get("senha")
    next_url = data.get("next")  # optional redirect after login

    professor = Professor.query.filter_by(email=email).first()
    if professor and professor.check_password(senha):
        session["usuario"] = {
            "tipo": "professor",
            "id": professor.id,
            "nome": professor.nome,
            "email": professor.email
        }
        redirect_to = next_url or url_for("html_bp.professor_dashboard")
        return jsonify({"mensagem": "Login realizado com sucesso", "tipo": "professor", "redirect": redirect_to}), 200

    return jsonify({"erro": "Credenciais inválidas"}), 401

# -----------------------------
# Login de Aluno (API)
# -----------------------------
@api_bp.route("/api/login/aluno", methods=["POST"])
def api_login_aluno():
    data = request.get_json() or {}
    cpf = data.get("cpf")
    email = data.get("email")
    senha = data.get("senha")
    next_url = data.get("next")

    aluno = Aluno.query.filter_by(email=email, cpf=cpf).first()
    if aluno and aluno.check_password(senha):
        session["usuario"] = {
            "tipo": "aluno",
            "id": aluno.id,
            "nome": aluno.nome,
            "cpf": aluno.cpf,
            "email": aluno.email
        }
        redirect_to = next_url or url_for("html_bp.aluno_dashboard")
        approval_status = (aluno.approval_status or "approved").lower()
        return jsonify({
            "mensagem": "Login realizado com sucesso",
            "tipo": "aluno",
            "redirect": redirect_to,
            "approval_status": approval_status,
        }), 200

    return jsonify({"erro": "Credenciais inválidas"}), 401

#-----------------------------
# Simulados Livres do Aluno
#-----------------------------
@api_bp.route("/api/simulados_livres", methods=["GET"])
@api_login_required_aluno
def listar_simulados_livres():
    aluno_id = session["usuario"]["id"]
    simulados = SimuladoLivre.query.filter_by(aluno_id=aluno_id).order_by(SimuladoLivre.data_realizacao.desc()).all()

    data = []
    for s in simulados:
        data.append({
            "id": s.id,
            "banco": s.banco,
            "pontuacao": s.pontuacao,
            "acertos": s.acertos,
            "data_realizacao": s.data_realizacao.strftime("%d/%m/%Y %H:%M")
        })

    return jsonify(data)

#-----------------------------
# Resultados do Aluno (simulados de turma + simulados livres)
#-----------------------------
@api_bp.route("/api/aluno_result/<cpf>", methods=["GET"])
def api_aluno_result(cpf):
    aluno = Aluno.query.filter_by(cpf=cpf).first_or_404()
    resultados = []

    simulados_livres = (
        SimuladoLivre.query.filter_by(aluno_id=aluno.id)
        .order_by(SimuladoLivre.data_realizacao.desc())
        .all()
    )

    for sl in simulados_livres:
        inicio_tentativa = sl.data_realizacao.replace(microsecond=0)
        fim_tentativa = inicio_tentativa + timedelta(seconds=1)

        respostas_livres = (
            Resposta.query.filter(
                Resposta.aluno_id == aluno.id,
                Resposta.banco == sl.banco,
                Resposta.turma_id.is_(None),
                Resposta.data_envio >= inicio_tentativa,
                Resposta.data_envio < fim_tentativa,
            )
            .order_by(Resposta.data_envio.desc())
            .all()
        )

        results, total_questions, total_correct = build_results_from_respostas(respostas_livres)

        nota = round((total_correct / total_questions) * 10, 1) if total_questions > 0 else 0

        resultados.append({
            "data": inicio_tentativa.strftime("%d/%m/%Y %H:%M:%S"),
            "data_iso": inicio_tentativa.strftime("%Y-%m-%dT%H:%M:%S"),
            "turma_id": None,
            "sheet_name": f"Simulado Livre - {sl.banco}",
            "total_correct": total_correct,
            "total_questions": total_questions,
            "score": nota,
            "results": results,
            "tipo": "livre",
        })

    resultados.sort(key=lambda x: x.get("data_iso", ""), reverse=True)

    return jsonify({"resultados": resultados})

#-----------------------------
# Resultados do Aluno por Turma (detalhado)
#-----------------------------
@api_bp.route("/api/aluno_result_turma/<int:turma_id>", methods=["GET"])
@api_login_required_aluno
def api_aluno_result_turma(turma_id):
    aluno_id = session["usuario"]["id"]
    aluno = Aluno.query.get_or_404(aluno_id)

    respostas = Resposta.query.filter_by(aluno_id=aluno.id, turma_id=turma_id) \
                              .order_by(Resposta.data_envio.desc()).all()

    resultados = []
    simulados_turma = {}

    for r in respostas:
        chave = f"{r.turma_id}-{r.data_envio.strftime('%Y%m%d%H%M%S')}"
        if chave not in simulados_turma:
            simulados_turma[chave] = {
                "data": r.data_envio.strftime("%d/%m/%Y %H:%M:%S"),
                "data_iso": r.data_envio.strftime("%Y-%m-%dT%H:%M:%S"),
                "turma_id": turma_id,
                "sheet_name": r.turma.sheet_name if r.turma else "Simulado de Turma",
                "results": [],
                "total_correct": 0,
                "total_questions": 0,
                "tipo": "turma",
                "respostas": []
            }
        simulados_turma[chave]["respostas"].append(r)

    for s in simulados_turma.values():
        results, total_questions, total_correct = build_results_from_respostas(s["respostas"])
        s["results"] = results
        s["total_questions"] = total_questions
        s["total_correct"] = total_correct
        s["score"] = round((s["total_correct"] / s["total_questions"]) * 10, 1) if s["total_questions"] > 0 else 0
        del s["respostas"]
        resultados.append(s)

    return jsonify({"resultados": resultados})

# -----------------------------
# Perfil e Logout
# -----------------------------
@api_bp.route('/api/perfil', methods=['GET'])
def perfil():
    if 'usuario' in session:
        return jsonify({"usuario": session['usuario']}), 200
    return jsonify({"erro": "Nenhum usuário logado"}), 401

@api_bp.route('/api/logout', methods=['POST'])
def logout():
    session.pop('usuario', None)
    return jsonify({"mensagem": "Logout realizado com sucesso"}), 200

# -----------------------------
# CRUD de Professores
# -----------------------------
@api_bp.route('/api/professores', methods=['POST'])
def criar_professor():
    data = request.get_json() or {}
    nome, email, senha = data.get('nome'), data.get('email'), data.get('senha')
    if Professor.query.filter_by(email=email).first():
        return jsonify({"erro": "Email já cadastrado"}), 400
    novo = Professor(nome=nome, email=email)
    novo.set_password(senha)
    db.session.add(novo)
    db.session.commit()
    return jsonify({"mensagem": "Professor criado com sucesso", "id": novo.id}), 201

@api_bp.route('/api/professores', methods=['GET'])
def listar_professores():
    professores = Professor.query.all()
    return jsonify([{"id": p.id, "nome": p.nome, "email": p.email} for p in professores])

# -----------------------------
# CRUD de Alunos
# -----------------------------
@api_bp.route('/api/alunos', methods=['POST'])
def criar_aluno():
    data = request.get_json() or {}
    nome, email, senha = data.get('nome'), data.get('email'), data.get('senha')
    if Aluno.query.filter_by(email=email).first():
        return jsonify({"erro": "Email já cadastrado"}), 400
    novo = Aluno(nome=nome, email=email)
    novo.set_password(senha)
    db.session.add(novo)
    db.session.commit()
    return jsonify({"mensagem": "Aluno criado com sucesso", "id": novo.id}), 201

@api_bp.route('/api/alunos', methods=['GET'])
def listar_alunos():
    alunos = Aluno.query.all()
    return jsonify([{"id": a.id, "nome": a.nome, "email": a.email} for a in alunos])

# -----------------------------
# CRUD de Turmas
# -----------------------------
@api_bp.route('/api/turmas', methods=['POST'])
def criar_turma():
    data = request.get_json() or {}
    nova = Turma(
        nome=data.get('nome'),
        disciplina=data.get('disciplina'),
        data=data.get('data'),
        professor_id=data.get('professor_id')
    )
    db.session.add(nova)
    db.session.commit()
    return jsonify({"mensagem": "Turma criada com sucesso", "id": nova.id}), 201

@api_bp.route('/api/turmas', methods=['GET'])
def listar_turmas():
    turmas = Turma.query.all()
    return jsonify([{"id": t.id, "nome": t.nome, "disciplina": t.disciplina, "data": str(t.data), "professor_id": t.professor_id} for t in turmas])

# -----------------------------
# CRUD de Matrículas
# -----------------------------
@api_bp.route('/api/matriculas', methods=['POST'])
def criar_matricula():
    data = request.get_json() or {}
    aluno_id, turma_id = data.get('aluno_id'), data.get('turma_id')
    if Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).first():
        return jsonify({"erro": "Aluno já matriculado nesta turma"}), 400
    nova = Matricula(aluno_id=aluno_id, turma_id=turma_id)
    db.session.add(nova)
    db.session.commit()
    return jsonify({"mensagem": "Matrícula criada com sucesso", "id": nova.id}), 201

@api_bp.route('/api/matriculas', methods=['GET'])
def listar_matriculas():
    matriculas = Matricula.query.all()
    return jsonify([{"id": m.id, "aluno_id": m.aluno_id, "turma_id": m.turma_id, "pronto": m.pronto} for m in matriculas])

#------------------------------
# Resultados da Turma
#------------------------------
@api_bp.route("/api/turma_result/<int:turma_id>", methods=["GET"])
def turma_result(turma_id):
    turma = Turma.query.get_or_404(turma_id)

    matriculas = Matricula.query.filter_by(turma_id=turma_id).all()

    alunos_data = []
    for m in matriculas:
        aluno = m.aluno
        respostas = Resposta.query.filter_by(matricula_id=m.id).all()

        total_questions = len(respostas)
        total_correct = sum(1 for r in respostas if r.correta)
        score = total_correct

        aluno_info = {
            "nome": aluno.nome,
            "cpf": aluno.cpf,
            "email": aluno.email,
            "score": score,
            "total_correct": total_correct,
            "total_questions": total_questions,
            "data": respostas[-1].data_envio.strftime("%d/%m/%Y %H:%M") if respostas else "",
            "sheet_name": turma.sheet_name,
            "results": [
                {
                    "question_text": r.questao,
                    "user_option": r.resposta,
                    "correct_option": "✔" if r.correta else "✘",
                    "correct": r.correta
                }
                for r in respostas
            ]
        }
        alunos_data.append(aluno_info)

    return jsonify({"alunos": alunos_data})

#-----------------------------
# Status da Sala de Espera
@api_bp.route("/api/sala_espera/<int:turma_id>")
def api_sala_espera(turma_id):
    turma = Turma.query.get_or_404(turma_id)
    alunos = [
        {"nome": m.aluno.nome, "pronto": m.pronto}
        for m in Matricula.query.filter_by(turma_id=turma.id).all()
    ]
    return jsonify({
        "status": turma.status.lower(),
        "alunos": alunos,
        "sheet_name": turma.sheet_name,
        "questoes": [q.id for q in turma.questoes] if turma.questoes else []
    })

# -----------------------------
# Webhook do Stripe para pagamentos PIX
# -----------------------------
@api_bp.route("/api/webhook/stripe", methods=["POST"])
def stripe_webhook():
    if stripe is None:
        return jsonify({"error": "Integração Stripe indisponível neste ambiente."}), 503

    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('stripe-signature')

    try:
        # Verificar assinatura do webhook (importante para segurança)
        # event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)

        # Para desenvolvimento, vamos aceitar sem verificação de assinatura
        # Em produção, configure o endpoint_secret e verifique a assinatura
        import json
        event = json.loads(payload)

    except ValueError:
        # Payload inválido
        return jsonify({"error": "Invalid payload"}), 400
    except Exception:
        # Assinatura inválida ou outro erro do Stripe
        return jsonify({"error": "Invalid signature"}), 400

    # Processar o evento
    if event['type'] == 'checkout.session.completed':
        session_data = event['data']['object']

        # Verificar se é um pagamento PIX
        if session_data.get('payment_status') == 'paid':
            # Buscar o payment_id nos metadados
            payment_id = session_data.get('metadata', {}).get('payment_id')

            if payment_id:
                # Atualizar o status do pagamento
                payment = Payment.query.get(int(payment_id))
                if payment and payment.status == 'pending':
                    payment.status = 'completed'
                    payment.stripe_session_id = session_data['id']
                    payment.completed_at = db.func.now()

                    # Ativar premium do professor
                    professor = payment.professor
                    if professor:
                        professor.premium_active = True
                        professor.premium_expires_at = datetime.utcnow() + timedelta(days=30)

                    db.session.commit()

                    print(f"Pagamento PIX confirmado automaticamente: {payment_id}")

    return jsonify({"status": "success"}), 200