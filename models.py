# models.py
from extensions import db   # ✅ importa o db já inicializado
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from datetime import datetime
from sqlalchemy import event

# ------------------------
# Simulados Livres (opcional, para simulações sem turma)
# ------------------------
class SimuladoLivre(db.Model):
    __tablename__ = "simulados_livres"

    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey("alunos.id"), nullable=False)

    # 🔹 Tipo do banco (HFC ou GPON)
    banco = db.Column(db.String(20), nullable=False)

    # 🔹 Pontuação final do simulado
    pontuacao = db.Column(db.Integer, nullable=False)

    # 🔹 Número de acertos
    acertos = db.Column(db.Integer, nullable=False)

    # 🔹 Data/hora da realização
    data_realizacao = db.Column(db.DateTime, default=datetime.utcnow)

    # 🔹 Relacionamento com o aluno
    aluno = db.relationship("Aluno", backref=db.backref("simulados_livres", lazy=True))

# ------------------------
# Professores
# ------------------------
class Professor(UserMixin, db.Model):
    __tablename__ = "professores"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    senha_hash = db.Column(db.Text, nullable=False)
    invite_code = db.Column(db.String(30), unique=True, nullable=True, index=True)

    # 🔹 Campos para assinatura premium
    is_premium = db.Column(db.Boolean, default=False)
    premium_expires_at = db.Column(db.DateTime, nullable=True)
    is_admin = db.Column(db.Boolean, default=False)  # Para o criador

    turmas = db.relationship("Turma", back_populates="professor", lazy=True)
    alunos = db.relationship("Aluno", back_populates="professor", lazy=True)

    def set_password(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_password(self, senha):
        return check_password_hash(self.senha_hash, senha)

    def is_premium_active(self):
        if self.is_admin:
            return True
        if not self.is_premium:
            return False
        if self.premium_expires_at is None:
            return False
        return self.premium_expires_at > datetime.utcnow()


# ------------------------
# Pagamentos
class Payment(db.Model):
    __tablename__ = "pagamentos"

    id = db.Column(db.Integer, primary_key=True)
    professor_id = db.Column(db.Integer, db.ForeignKey("professores.id"), nullable=False)
    method = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(30), nullable=False, default="pending")
    description = db.Column(db.String(255), nullable=True)
    external_ref = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    professor = db.relationship("Professor", backref=db.backref("payments", lazy=True))


# ------------------------
# Turmas
# ------------------------
class Turma(db.Model):
    __tablename__ = "turmas"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    disciplina = db.Column(db.String(100), nullable=False)
    data = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default="Aguardando")
    auto_restart_enabled = db.Column(db.Boolean, default=False, nullable=False)
    exibir_respostas = db.Column(db.Boolean, default=True, nullable=False)

    professor_id = db.Column(db.Integer, db.ForeignKey("professores.id"), nullable=False)
    sheet_name = db.Column(db.String(50), nullable=True)

    professor = db.relationship("Professor", back_populates="turmas")
    matriculas = db.relationship("Matricula", back_populates="turma", lazy=True, cascade="all, delete-orphan")
    respostas = db.relationship("Resposta", back_populates="turma", lazy=True, cascade="all, delete-orphan")

    # 🔹 Relacionamento com questões escolhidas manualmente
    questoes = db.relationship(
        "Questao",
        secondary="questoes_turma",   # nome da tabela associativa
        backref="turmas",
        lazy=True
    )

# ------------------------
# Alunos
# ------------------------
class Aluno(db.Model):
    __tablename__ = "alunos"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    cpf = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    empresa = db.Column(db.String(120), nullable=True)
    professor_id = db.Column(db.Integer, db.ForeignKey("professores.id"), nullable=True)
    approval_status = db.Column(db.String(20), nullable=False, default="approved")
    approved_at = db.Column(db.DateTime, nullable=True)
    invite_code_used = db.Column(db.String(30), nullable=True)

    professor = db.relationship("Professor", back_populates="alunos", lazy=True)
    matriculas = db.relationship("Matricula", back_populates="aluno", cascade="all, delete-orphan")
    respostas = db.relationship("Resposta", back_populates="aluno", lazy=True, cascade="all, delete-orphan")

    def set_password(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_password(self, senha):
        return check_password_hash(self.senha_hash, senha)

    def is_approved(self):
        return (self.approval_status or "approved").lower() == "approved"

    def __repr__(self):
        return f"<Aluno {self.nome} ({self.email})>"


# ------------------------
# Matrículas (Aluno em Turma)
# ------------------------
class Matricula(db.Model):
    __tablename__ = "matriculas"

    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey("alunos.id"), nullable=False)
    turma_id = db.Column(db.Integer, db.ForeignKey("turmas.id", ondelete="CASCADE"), nullable=False)
    pronto = db.Column(db.Boolean, default=False)
    finalizou = db.Column(db.Boolean, default=False, nullable=False)

    aluno = db.relationship("Aluno", back_populates="matriculas", lazy=True)
    turma = db.relationship("Turma", back_populates="matriculas", lazy=True)
    respostas = db.relationship("Resposta", back_populates="matricula", lazy=True)

    def __repr__(self):
        return f"<Matricula aluno={self.aluno_id} turma={self.turma_id}>"


# ------------------------
# Questões
# ------------------------
class Questao(db.Model):
    __tablename__ = "questao"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    texto = db.Column(db.Text, nullable=False)
    opcao_a = db.Column(db.Text)
    opcao_b = db.Column(db.Text)
    opcao_c = db.Column(db.Text)
    opcao_d = db.Column(db.Text)
    correta = db.Column(db.Text)
    imagem = db.Column(db.Text)   # URL da imagem
    banco = db.Column(db.Text)    # ex: "GPON" ou "HFC"
    tema = db.Column(db.String(120), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "texto": self.texto,
            "opcoes": [
                {"label": "a", "texto": self.opcao_a},
                {"label": "b", "texto": self.opcao_b},
                {"label": "c", "texto": self.opcao_c},
                {"label": "d", "texto": self.opcao_d},
            ],
            "correta": self.correta,
            "imagem": self.imagem,
            "banco": self.banco,
            "tema": self.tema,
        }

# ------------------------
# Respostas dos alunos
# ------------------------
class Resposta(db.Model):
    __tablename__ = "respostas"

    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey("alunos.id"), nullable=False)

    # 🔹 Agora opcionais
    turma_id = db.Column(db.Integer, db.ForeignKey("turmas.id", ondelete="CASCADE"), nullable=True)
    matricula_id = db.Column(db.Integer, db.ForeignKey("matriculas.id", ondelete="CASCADE"), nullable=True)

    questao_id = db.Column(db.Integer, db.ForeignKey("questao.id"), nullable=False)
    resposta = db.Column(db.String(255), nullable=False)
    correta = db.Column(db.Boolean, default=False)
    data_envio = db.Column(db.DateTime, default=db.func.now())

    # 🔹 Novo campo para simulados livres
    banco = db.Column(db.String(20), nullable=True)
    tipo = db.Column(db.String(20), nullable=False)  # "livre" ou "turma"

    aluno = db.relationship("Aluno", back_populates="respostas", lazy=True)
    turma = db.relationship("Turma", back_populates="respostas", lazy=True)
    matricula = db.relationship("Matricula", back_populates="respostas", lazy=True)
    questao = db.relationship("Questao")

# 🔹 Listener para preencher automaticamente o campo 'tipo'
@event.listens_for(Resposta, "before_insert")
def set_tipo(mapper, connection, target):
    if target.turma_id:
        target.tipo = "turma"
    else:
        target.tipo = "livre"

# ------------------------
# Questões escolhidas para turma
# ------------------------
class QuestaoTurma(db.Model):
    __tablename__ = "questoes_turma"
    id = db.Column(db.Integer, primary_key=True)
    turma_id = db.Column(db.Integer, db.ForeignKey("turmas.id"), nullable=False)
    questao_id = db.Column(db.Integer, db.ForeignKey("questao.id"), nullable=False)


# ------------------------
# Password Reset Tokens (senha)
# ------------------------
class PasswordReset(db.Model):
    """
    Armazena hash do token de reset, para usuário (aluno/professor).
    - token_hash: sha256 do token (não armazenamos token em texto)
    - user_type: 'aluno' ou 'professor'
    - user_id: id correspondente na tabela
    - created_at: datetime de criação
    - used: boolean para impedir reuso
    """
    __tablename__ = "password_resets"

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    user_type = db.Column(db.String(20), nullable=False)
    user_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    used = db.Column(db.Boolean, default=False, nullable=False)

    def mark_used(self):
        self.used = True