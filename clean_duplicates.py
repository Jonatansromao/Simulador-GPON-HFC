from app import app
from extensions import db
from models import Questao

def limpar_duplicatas():
    with app.app_context():
        # Encontrar duplicatas baseadas em texto e banco
        duplicatas = db.session.query(Questao.texto, Questao.banco, db.func.count(Questao.id)).\
            group_by(Questao.texto, Questao.banco).\
            having(db.func.count(Questao.id) > 1).all()

        print(f"Encontradas {len(duplicatas)} questões duplicadas.")

        for texto, banco, count in duplicatas:
            # Manter apenas a primeira ocorrência (menor id)
            questoes = Questao.query.filter_by(texto=texto, banco=banco).order_by(Questao.id).all()
            # Deletar todas exceto a primeira
            for questao in questoes[1:]:
                print(f"Deletando duplicata: ID {questao.id} - {texto[:50]}...")
                db.session.delete(questao)

        db.session.commit()
        print("Duplicatas removidas com sucesso!")

if __name__ == "__main__":
    limpar_duplicatas()