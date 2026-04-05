# extensions.py
import os
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO

db = SQLAlchemy()
socketio = SocketIO(async_mode=os.getenv("SOCKETIO_ASYNC_MODE", "threading"), cors_allowed_origins="*")
