# extensions.py
import os
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO

db = SQLAlchemy()
default_async_mode = os.getenv("SOCKETIO_ASYNC_MODE")
if not default_async_mode and os.getenv("APP_ENV", "").lower() == "production":
    default_async_mode = "eventlet"
socketio = SocketIO(async_mode=default_async_mode or "threading", cors_allowed_origins="*")
