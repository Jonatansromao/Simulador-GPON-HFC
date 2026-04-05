import os
from flask import Flask
from flask_migrate import Migrate
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

with app.app_context():
    db.create_all()

app.register_blueprint(html_bp)
app.register_blueprint(api_bp)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
    socketio.run(app, host="0.0.0.0", port=port, debug=debug_mode)
