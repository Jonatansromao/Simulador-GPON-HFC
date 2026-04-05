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
app.secret_key = "your_secret_key"

database_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "instance", "simulador.db")
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
    socketio.run(app, debug=True)
