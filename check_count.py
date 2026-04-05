from app import app
from extensions import db
from models import Questao

with app.app_context():
    total = Questao.query.count()
    print(f"Total de questões após limpeza: {total}")