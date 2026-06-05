"""
Script de validação de segurança para produção
Execute antes de fazer deploy: python validate_production.py
"""
import os
import sys
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def check_production_env():
    """Valida configurações essenciais para produção"""
    
    is_production = os.getenv("APP_ENV", "").lower() == "production" or os.getenv("RENDER", "").lower() == "true"
    
    if not is_production:
        logger.warning("⚠️  Não está em modo produção. Pulando validações.")
        return True
    
    logger.info("🔍 Validando configurações de PRODUÇÃO...")
    
    checks = {
        "✅ DATABASE_URL": bool(os.getenv("DATABASE_URL")),
        "✅ SECRET_KEY explícito": bool(os.getenv("SECRET_KEY")),
        "✅ SMTP_PASS": bool(os.getenv("SMTP_PASS")),
        "✅ APP_BASE_URL ou PRODUCTION_URL": bool(
            os.getenv("APP_BASE_URL") or os.getenv("PRODUCTION_URL")
        ),
        "✅ FLASK_DEBUG=false": os.getenv("FLASK_DEBUG", "").lower() in ("false", "0", "no"),
    }
    
    # Pagamentos opcionais (pelo menos 1 deve estar configurado)
    payment_configured = any([
        os.getenv("STRIPE_SECRET_KEY"),
        os.getenv("MERCADOPAGO_ACCESS_TOKEN"),
    ])
    checks["✅ Pelo menos 1 gateway de pagamento"] = payment_configured
    
    all_passed = True
    for check, result in checks.items():
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{status}: {check}")
        if not result:
            all_passed = False
    
    # Avisos
    logger.info("\n📢 Verificações Adicionais:")
    
    if is_production and os.getenv("SOCKETIO_ALLOWED_ORIGINS") == "*":
        logger.warning("⚠️  SOCKETIO_ALLOWED_ORIGINS está aberto para todo mundo!")
        all_passed = False
    else:
        logger.info("✅ SOCKETIO_ALLOWED_ORIGINS configurado")
    
    if not os.getenv("STRIPE_WEBHOOK_SECRET") and os.getenv("STRIPE_SECRET_KEY"):
        logger.warning("⚠️  STRIPE_WEBHOOK_SECRET não configurado (webhooks podem falhar)")
    
    if os.getenv("LOG_LEVEL", "INFO") == "DEBUG" and is_production:
        logger.warning("⚠️  LOG_LEVEL=DEBUG em produção (vazará dados sensíveis!)")
        all_passed = False
    
    # Resultado final
    print("\n" + "=" * 60)
    if all_passed:
        logger.info("🎉 Todas as verificações PASSARAM! Seguro fazer deploy.")
        return True
    else:
        logger.error("❌ Algumas verificações FALHARAM. Verifique as configurações antes de fazer deploy.")
        return False

if __name__ == "__main__":
    success = check_production_env()
    sys.exit(0 if success else 1)
