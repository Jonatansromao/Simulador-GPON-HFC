"""
Configurações centralizadas da aplicação
Valida e organiza todas as variáveis de ambiente
"""
import os
import logging
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

# ========== AMBIENTE ==========
APP_ENV = os.getenv("APP_ENV", "development").lower()
IS_PRODUCTION = APP_ENV == "production" or os.getenv("RENDER", "").lower() == "true"
DEBUG = not IS_PRODUCTION

# ========== TIMEZONE ==========
try:
    APP_TIMEZONE = ZoneInfo(os.getenv("APP_TIMEZONE", "America/Sao_Paulo"))
except Exception:
    APP_TIMEZONE = timezone(timedelta(hours=-3))

# ========== SEGURANÇA - SECRET KEY ==========
def resolve_secret_key() -> str:
    """Resolve a chave secreta com fallback seguro"""
    import secrets
    import hashlib
    
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
        logging.warning("⚠️  SECRET_KEY não definido em produção. Usando chave gerada (não persiste entre restarts)")
        return secrets.token_urlsafe(48)

    return "dev-secret-key-change-me"

SECRET_KEY = resolve_secret_key()

# ========== DATABASE ==========
database_url = os.getenv("DATABASE_URL")
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = database_url
else:
    base_dir = os.path.abspath(os.path.dirname(__file__))
    instance_dir = os.path.join(base_dir, "instance")
    os.makedirs(instance_dir, exist_ok=True)
    database_path = os.path.join(instance_dir, "simulador.db")
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{database_path}"

SQLALCHEMY_TRACK_MODIFICATIONS = False

# ========== SESSÃO ==========
SESSION_LIFETIME_HOURS = int(os.getenv("SESSION_LIFETIME_HOURS", "12"))
PERMANENT_SESSION_LIFETIME = timedelta(hours=SESSION_LIFETIME_HOURS)
SESSION_REFRESH_EACH_REQUEST = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = IS_PRODUCTION
SESSION_COOKIE_SAMESITE = "Lax"
REMEMBER_COOKIE_HTTPONLY = True
REMEMBER_COOKIE_SECURE = IS_PRODUCTION

# ========== UPLOAD & CONTEÚDO ==========
MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", 10 * 1024 * 1024))  # 10MB

# ========== APLICAÇÃO ==========
APP_VERSION = os.getenv("APP_VERSION", "1.5")
TUTORIAL_PROFESSOR_URL = os.getenv("TUTORIAL_PROFESSOR_URL", "")
TUTORIAL_ALUNO_URL = os.getenv("TUTORIAL_ALUNO_URL", "")

# ========== URL BASE (para webhooks e redirects) ==========
def get_base_url(request_base_url=None) -> str:
    """Resolve URL base segura para produção"""
    candidates = [
        request_base_url,
        os.getenv("APP_BASE_URL"),
        os.getenv("RAILWAY_PUBLIC_DOMAIN"),
        os.getenv("RENDER_EXTERNAL_URL"),
        os.getenv("NGROK_URL"),
    ]
    
    for candidate in candidates:
        if not candidate:
            continue
        normalized = str(candidate).strip().rstrip("/")
        if not normalized:
            continue
        
        # Validar que não é localhost/internal em produção
        lowered = normalized.lower()
        if IS_PRODUCTION and ("localhost" in lowered or "127.0.0.1" in lowered or ".internal" in lowered):
            continue
        
        if "://" not in normalized:
            normalized = f"https://{normalized}" if IS_PRODUCTION else f"http://{normalized}"
        
        return normalized
    
    if IS_PRODUCTION:
        # Tentar usar variável de ambiente específica
        prod_url = os.getenv("PRODUCTION_URL")
        if prod_url:
            return prod_url
        logging.warning("⚠️  APP_BASE_URL não definido em produção, usando localhost (PERIGO!)")
    
    return "http://localhost:5000"

# ========== RATE LIMITING ==========
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

# ========== PAGAMENTOS - STRIPE ==========
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

STRIPE_CONFIGURED = bool(STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY)
if IS_PRODUCTION and not STRIPE_CONFIGURED:
    logging.error("❌ STRIPE não configurado em produção!")

# ========== PAGAMENTOS - MERCADO PAGO ==========
MERCADOPAGO_ACCESS_TOKEN = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
MERCADOPAGO_SANDBOX = os.getenv("MERCADOPAGO_SANDBOX", "false").lower() in ("1", "true", "yes")

MERCADOPAGO_CONFIGURED = bool(MERCADOPAGO_ACCESS_TOKEN)
if IS_PRODUCTION and not MERCADOPAGO_CONFIGURED:
    logging.error("❌ MERCADO PAGO não configurado em produção!")

# ========== EMAIL (SMTP) ==========
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USER)

# ========== SOCKET.IO (WEBSOCKETS) ==========
def get_socketio_allowed_origins():
    """CORS seguro para WebSockets"""
    if IS_PRODUCTION:
        allowed = os.getenv("SOCKETIO_ALLOWED_ORIGINS", "https://simulador-gpon-hfc.me").split(",")
        return [origin.strip() for origin in allowed if origin.strip()]
    return "*"

SOCKETIO_ALLOWED_ORIGINS = get_socketio_allowed_origins()
SOCKETIO_ASYNC_MODE = os.getenv("SOCKETIO_ASYNC_MODE", "eventlet" if IS_PRODUCTION else "threading")

# ========== RESET TOKEN (RECUPERAÇÃO DE SENHA) ==========
RESET_TOKEN_EXPIRY = int(os.getenv("RESET_TOKEN_EXPIRY", "3600"))  # 1 hora

# ========== LOGGING ==========
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO" if IS_PRODUCTION else "DEBUG")
LOG_FORMAT = os.getenv("LOG_FORMAT", "json" if IS_PRODUCTION else "text")  # json ou text

# ========== VERIFICAÇÕES DE PRODUÇÃO ==========
if IS_PRODUCTION:
    checks = {
        "DATABASE_URL": bool(os.getenv("DATABASE_URL")),
        "SECRET_KEY explícito": bool(os.getenv("SECRET_KEY")),
        "SMTP_PASS": bool(os.getenv("SMTP_PASS")),
        "APP_BASE_URL ou RENDER_EXTERNAL_URL": bool(os.getenv("APP_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")),
    }
    
    failed = [k for k, v in checks.items() if not v]
    if failed:
        logging.warning(f"⚠️  Variáveis recomendadas em produção não definidas: {', '.join(failed)}")
