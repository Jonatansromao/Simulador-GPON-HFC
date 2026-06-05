"""
Utilitários compartilhados - evita duplicação de código
"""
import logging
import json
from datetime import datetime, timezone
from typing import Optional
from flask import request, current_app
import config


class JSONFormatter(logging.Formatter):
    """Formata logs em JSON para produção"""
    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Adicionar exceção se existir
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    """Cria logger configurado com base no ambiente"""
    logger = logging.getLogger(name)
    
    # Evitar duplicação de handlers
    if logger.handlers:
        return logger
    
    # Configurar nível
    log_level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    logger.setLevel(log_level)
    
    # Criar handler
    handler = logging.StreamHandler()
    handler.setLevel(log_level)
    
    # Configurar formatter
    if config.LOG_FORMAT == "json":
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s in %(module)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger


def prefers_json_response() -> bool:
    """Detecta se a requisição prefere resposta JSON"""
    return (
        request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.path.startswith("/api/")
        or (request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html)
    )


def get_client_identifier() -> str:
    """Identifica cliente para rate limiting e logs"""
    forwarded_for = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded_for or request.headers.get("X-Real-IP") or request.remote_addr or "unknown"


def format_datetime_local(value, fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Formata datetime para timezone local"""
    if not value:
        return ""
    
    if hasattr(value, 'replace'):
        localized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        return localized.astimezone(config.APP_TIMEZONE).strftime(fmt)
    
    return str(value)
