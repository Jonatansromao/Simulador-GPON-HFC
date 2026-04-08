from app import app
from extensions import db
from models import Questao
from import_csv import (
    merge_question_duplicates,
    normalize_question_text,
    recalculate_question_answers,
)


def limpar_duplicatas(banco=None):
    with app.app_context():
        query = Questao.query
        if banco:
            query = query.filter_by(banco=banco)

        questoes = query.order_by(Questao.banco.asc(), Questao.id.asc()).all()
        grupos = {}
        for questao in questoes:
            chave = (questao.banco, normalize_question_text(questao.texto))
            if not chave[1]:
                continue
            grupos.setdefault(chave, []).append(questao)

        grupos_duplicados = {chave: itens for chave, itens in grupos.items() if len(itens) > 1}
        print(f"Encontrados {len(grupos_duplicados)} grupos de questões duplicadas.")

        removidas = 0
        for (nome_banco, _), itens in grupos_duplicados.items():
            principal = itens[0]
            removidas += merge_question_duplicates(principal, itens[1:])
            print(f"Mantida questão ID {principal.id} no banco {nome_banco}; duplicatas mescladas: {len(itens) - 1}")

        db.session.commit()
        print(f"Duplicatas removidas com sucesso! Total removido: {removidas}.")


def recalcular_historicos(banco=None):
    with app.app_context():
        query = Questao.query
        if banco:
            query = query.filter_by(banco=banco)

        total_atualizadas = 0
        questoes = query.order_by(Questao.id.asc()).all()
        for questao in questoes:
            total_atualizadas += recalculate_question_answers(questao)

        db.session.commit()
        print(f"Históricos recalculados com sucesso! Respostas corrigidas: {total_atualizadas}.")


if __name__ == "__main__":
    limpar_duplicatas()
    recalcular_historicos()