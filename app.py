import os
import csv
from flask import Flask
from flask_migrate import Migrate
from sqlalchemy import inspect, text
from dotenv import load_dotenv

# Carregar variáveis de ambiente do arquivo .env antes dos imports que dependem delas
load_dotenv()

from extensions import db, socketio
from routes_html import html_bp
from routes_api import api_bp

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-me")

base_dir = os.path.abspath(os.path.dirname(__file__))
instance_dir = os.path.join(base_dir, "instance")
os.makedirs(instance_dir, exist_ok=True)

database_url = os.getenv("DATABASE_URL")
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    database_path = os.path.join(instance_dir, "simulador.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{database_path}"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
migrate = Migrate(app, db)
socketio.init_app(app)


def ensure_schema_updates():
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    if "professores" in tables:
        professor_columns = {col["name"] for col in inspector.get_columns("professores")}
        if "invite_code" not in professor_columns:
            db.session.execute(text("ALTER TABLE professores ADD COLUMN invite_code VARCHAR(30)"))
            db.session.commit()
        try:
            db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_professores_invite_code ON professores (invite_code)"))
            db.session.commit()
        except Exception:
            db.session.rollback()

    if "alunos" in tables:
        aluno_columns = {col["name"] for col in inspector.get_columns("alunos")}
        additions = {
            "empresa": "ALTER TABLE alunos ADD COLUMN empresa VARCHAR(120)",
            "professor_id": "ALTER TABLE alunos ADD COLUMN professor_id INTEGER",
            "approval_status": "ALTER TABLE alunos ADD COLUMN approval_status VARCHAR(20) DEFAULT 'approved'",
            "approved_at": "ALTER TABLE alunos ADD COLUMN approved_at DATETIME",
            "invite_code_used": "ALTER TABLE alunos ADD COLUMN invite_code_used VARCHAR(30)",
        }

        for column_name, statement in additions.items():
            if column_name not in aluno_columns:
                db.session.execute(text(statement))
                db.session.commit()

        db.session.execute(
            text("UPDATE alunos SET approval_status = 'approved' WHERE approval_status IS NULL OR approval_status = ''")
        )
        db.session.commit()


def ensure_question_banks_loaded():
    from models import Questao

    bank_files = {
        "GPON": "GPON.csv",
        "HFC": "HFC.csv",
    }

    for banco, filename in bank_files.items():
        if Questao.query.filter_by(banco=banco).count() > 0:
            continue

        csv_path = os.path.join(base_dir, filename)
        if not os.path.exists(csv_path):
            print(f"Arquivo {filename} não encontrado para importar {banco}.")
            continue

        added = 0
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                texto = (row.get("Pergunta") or "").strip()
                if not texto:
                    continue
                q = Questao(
                    texto=texto,
                    opcao_a=(row.get("A") or "").strip() or None,
                    opcao_b=(row.get("B") or "").strip() or None,
                    opcao_c=(row.get("C") or "").strip() or None,
                    opcao_d=(row.get("D") or "").strip() or None,
                    correta=(row.get("Correta") or "").strip() or None,
                    imagem=(row.get("Imagem") or "").strip() or None,
                    banco=banco,
                )
                db.session.add(q)
                added += 1

        db.session.commit()
        print(f"Banco {banco} carregado com {added} questões.")


with app.app_context():
    db.create_all()
    ensure_schema_updates()
    ensure_question_banks_loaded()

app.register_blueprint(html_bp)
app.register_blueprint(api_bp)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
    socketio.run(app, host="0.0.0.0", port=port, debug=debug_mode)
