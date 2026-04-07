import os
import csv
import time
import secrets
import hashlib
from datetime import timedelta
from flask import Flask, jsonify, request, session, flash, redirect, url_for
from flask_migrate import Migrate
from sqlalchemy import inspect, text
from dotenv import load_dotenv
from cachelib import SimpleCache
from werkzeug.middleware.proxy_fix import ProxyFix

# Carregar variáveis de ambiente do arquivo .env antes dos imports que dependem delas
load_dotenv()

from extensions import db, socketio
from routes_html import html_bp
from routes_api import api_bp

IS_PRODUCTION = os.getenv("RENDER", "").lower() == "true" or os.getenv("APP_ENV", "").lower() == "production"


def resolve_secret_key() -> str:
    explicit_secret = os.getenv("SECRET_KEY")
    if explicit_secret:
        return explicit_secret

    derived_seed = "|".join(
        value
        for value in [
            os.getenv("DATABASE_URL"),
            os.getenv("SMTP_PASS"),
            os.getenv("MERCADOPAGO_ACCESS_TOKEN"),
            os.getenv("STRIPE_SECRET_KEY"),
        ]
        if value
    )
    if derived_seed:
        return hashlib.sha256(derived_seed.encode("utf-8")).hexdigest()

    if IS_PRODUCTION:
        generated_secret = secrets.token_urlsafe(48)
        print("WARNING: SECRET_KEY não definido em produção. Uma chave temporária foi gerada para este processo.")
        return generated_secret

    return "dev-secret-key-change-me"


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["SECRET_KEY"] = resolve_secret_key()
app.secret_key = app.config["SECRET_KEY"]

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
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", 10 * 1024 * 1024))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SECURE"] = IS_PRODUCTION
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=int(os.getenv("SESSION_LIFETIME_HOURS", "12")))
app.config["SESSION_REFRESH_EACH_REQUEST"] = False

db.init_app(app)
migrate = Migrate(app, db)
socketio.init_app(app)

rate_limit_cache = SimpleCache(default_timeout=900)
SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
CSRF_EXEMPT_PATH_PREFIXES = ("/webhook/", "/api/webhook/", "/socket.io")
RATE_LIMIT_RULES = [
    ("/login/professor", 10, 300),
    ("/login/aluno", 10, 300),
    ("/api/login/professor", 15, 300),
    ("/api/login/aluno", 15, 300),
    ("/professor/register", 6, 600),
    ("/aluno/register", 8, 600),
    ("/forgot_password", 5, 900),
    ("/professor/pagar_", 4, 300),
    ("/professor/assinar_premium", 4, 300),
]


def prefers_json_response() -> bool:
    return (
        request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.path.startswith("/api/")
        or (request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html)
    )


def client_identifier() -> str:
    forwarded_for = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded_for or request.headers.get("X-Real-IP") or request.remote_addr or "unknown"


def generate_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.context_processor
def inject_security_helpers():
    return {"csrf_token": generate_csrf_token}


@app.before_request
def enforce_security_controls():
    if session.get("usuario"):
        session.permanent = True

    path = (request.path or "/").lower()

    if request.method == "POST":
        for prefix, limit, window in RATE_LIMIT_RULES:
            if path.startswith(prefix):
                cache_key = f"ratelimit:{prefix}:{client_identifier()}"
                bucket = rate_limit_cache.get(cache_key)
                now = time.time()

                if not bucket or now >= bucket["reset_at"]:
                    bucket = {"count": 0, "reset_at": now + window}

                bucket["count"] += 1
                rate_limit_cache.set(cache_key, bucket, timeout=window)

                if bucket["count"] > limit:
                    retry_after = max(1, int(bucket["reset_at"] - now))
                    message = f"Muitas tentativas nesta área. Aguarde {retry_after}s e tente novamente."
                    if prefers_json_response():
                        return jsonify({"success": False, "message": message}), 429
                    flash(message, "warning")
                    return redirect(request.referrer or url_for("html_bp.home"))
                break

    if request.method in SAFE_HTTP_METHODS:
        return None

    if any(path.startswith(prefix) for prefix in CSRF_EXEMPT_PATH_PREFIXES):
        return None

    expected_token = session.get("_csrf_token")
    provided_token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")

    if not expected_token or not provided_token or not secrets.compare_digest(str(provided_token), str(expected_token)):
        message = "Sessão expirada ou solicitação inválida. Atualize a página e tente novamente."
        if prefers_json_response():
            return jsonify({"success": False, "message": message}), 400
        flash(message, "warning")
        return redirect(request.referrer or url_for("html_bp.home"))

    return None


@app.after_request
def apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if IS_PRODUCTION:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def ensure_schema_updates():
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    dialect_name = db.engine.dialect.name.lower()
    timestamp_sql = "TIMESTAMP" if "postgres" in dialect_name else "DATETIME"

    def execute_statement(statement: str):
        db.session.execute(text(statement))
        db.session.commit()

    if "professores" in tables:
        professor_columns = {col["name"] for col in inspector.get_columns("professores")}
        if "invite_code" not in professor_columns:
            execute_statement("ALTER TABLE professores ADD COLUMN invite_code VARCHAR(30)")
        try:
            execute_statement("CREATE UNIQUE INDEX IF NOT EXISTS ix_professores_invite_code ON professores (invite_code)")
        except Exception:
            db.session.rollback()

    if "alunos" in tables:
        aluno_columns = {col["name"] for col in inspector.get_columns("alunos")}
        additions = {
            "empresa": "ALTER TABLE alunos ADD COLUMN empresa VARCHAR(120)",
            "professor_id": "ALTER TABLE alunos ADD COLUMN professor_id INTEGER",
            "approval_status": "ALTER TABLE alunos ADD COLUMN approval_status VARCHAR(20)",
            "approved_at": f"ALTER TABLE alunos ADD COLUMN approved_at {timestamp_sql}",
            "invite_code_used": "ALTER TABLE alunos ADD COLUMN invite_code_used VARCHAR(30)",
        }

        for column_name, statement in additions.items():
            if column_name not in aluno_columns:
                execute_statement(statement)

        execute_statement(
            "UPDATE alunos SET approval_status = 'approved' WHERE approval_status IS NULL OR approval_status = ''"
        )

    if "turmas" in tables:
        turma_columns = {col["name"] for col in inspector.get_columns("turmas")}
        if "auto_restart_enabled" not in turma_columns:
            execute_statement("ALTER TABLE turmas ADD COLUMN auto_restart_enabled BOOLEAN DEFAULT FALSE")
        execute_statement(
            "UPDATE turmas SET auto_restart_enabled = FALSE WHERE auto_restart_enabled IS NULL"
        )


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
