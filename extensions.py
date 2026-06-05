# extensions.py
import os
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO

db = SQLAlchemy()
default_async_mode = os.getenv("SOCKETIO_ASYNC_MODE")
if not default_async_mode and os.getenv("APP_ENV", "").lower() == "production":
    default_async_mode = "eventlet"

# 🔒 CORS configurado com whitelist seguro
def get_allowed_origins():
    if os.getenv("APP_ENV", "").lower() == "production":
        allowed = os.getenv("SOCKETIO_ALLOWED_ORIGINS", "https://simulador-gpon-hfc.me").split(",")
        return [origin.strip() for origin in allowed if origin.strip()]
    return "*"

socketio = SocketIO(
    async_mode=default_async_mode or "threading",
    cors_allowed_origins=get_allowed_origins(),
    cors_credentials=True
)
