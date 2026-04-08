import os
import re

import pandas as pd

from app import app
from extensions import db
from models import Questao, QuestaoTurma, Resposta

def normalize_question_text(texto):
    texto = (texto or "").strip().casefold()
    return re.sub(r"\s+", " ", texto)


def recalculate_question_answers(questao):
    if not questao:
        return 0

    correta = (questao.correta or "").strip().upper()[:1]
    respostas = Resposta.query.filter_by(questao_id=questao.id).all()
    atualizadas = 0

    for resposta in respostas:
        resposta_normalizada = (resposta.resposta or "").strip().upper()[:1]
        novo_status = bool(resposta_normalizada and correta and resposta_normalizada == correta)
        if bool(resposta.correta) != novo_status:
            resposta.correta = novo_status
            atualizadas += 1

    return atualizadas


def merge_question_duplicates(questao_principal, duplicadas):
    removidas = 0
    turma_links_existentes = {
        link.turma_id for link in QuestaoTurma.query.filter_by(questao_id=questao_principal.id).all()
    }

    for duplicada in duplicadas:
        if not duplicada or duplicada.id == questao_principal.id:
            continue

        Resposta.query.filter_by(questao_id=duplicada.id).update(
            {"questao_id": questao_principal.id},
            synchronize_session=False,
        )

        for link in QuestaoTurma.query.filter_by(questao_id=duplicada.id).all():
            if link.turma_id in turma_links_existentes:
                db.session.delete(link)
            else:
                link.questao_id = questao_principal.id
                turma_links_existentes.add(link.turma_id)

        db.session.delete(duplicada)
        removidas += 1

    return removidas


def importar_csv(nome_arquivo, banco):
    caminho_arquivo = os.path.join(os.path.dirname(__file__), nome_arquivo)
    df = pd.read_csv(caminho_arquivo)

    with app.app_context():
        criadas = 0
        atualizadas = 0
        sem_alteracao = 0
        duplicadas_removidas = 0
        respostas_recalculadas = 0

        existentes_por_chave = {}
        questoes_existentes = Questao.query.filter_by(banco=banco).order_by(Questao.id.asc()).all()
        for questao in questoes_existentes:
            chave = normalize_question_text(questao.texto)
            if not chave:
                continue
            existentes_por_chave.setdefault(chave, []).append(questao)

        for _, row in df.iterrows():
            texto = None if pd.isna(row.get("Pergunta")) else str(row["Pergunta"]).strip()
            if not texto:
                continue

            tema_csv = None if pd.isna(row.get("Tema")) else str(row.get("Tema")).strip()
            dados_questao = {
                "texto": texto,
                "opcao_a": None if pd.isna(row.get("A")) else str(row["A"]).strip(),
                "opcao_b": None if pd.isna(row.get("B")) else str(row["B"]).strip(),
                "opcao_c": None if pd.isna(row.get("C")) else str(row["C"]).strip(),
                "opcao_d": None if pd.isna(row.get("D")) else str(row["D"]).strip(),
                "correta": None if pd.isna(row.get("Correta")) else str(row.get("Correta")).strip().upper()[:1],
                "imagem": None if pd.isna(row.get("Imagem")) else str(row["Imagem"]).strip(),
                "banco": banco,
            }
            if tema_csv:
                dados_questao["tema"] = tema_csv

            chave = normalize_question_text(texto)
            existentes = existentes_por_chave.get(chave, [])

            if existentes:
                questao_principal = existentes[0]
                removidas_neste_grupo = merge_question_duplicates(questao_principal, existentes[1:])
                duplicadas_removidas += removidas_neste_grupo
                existentes_por_chave[chave] = [questao_principal]

                houve_mudanca = False
                for campo, novo_valor in dados_questao.items():
                    if campo == "tema" and not novo_valor:
                        continue
                    valor_atual = getattr(questao_principal, campo)
                    if valor_atual != novo_valor:
                        setattr(questao_principal, campo, novo_valor)
                        houve_mudanca = True

                if houve_mudanca:
                    atualizadas += 1
                    print(f"Questão atualizada: {texto[:60]}...")
                else:
                    sem_alteracao += 1

                if houve_mudanca or removidas_neste_grupo:
                    respostas_recalculadas += recalculate_question_answers(questao_principal)
                continue

            q = Questao(**dados_questao)
            db.session.add(q)
            db.session.flush()
            existentes_por_chave[chave] = [q]
            criadas += 1

        db.session.commit()
        print(
            f"Questões {banco} sincronizadas com sucesso! "
            f"Criadas: {criadas}, atualizadas: {atualizadas}, "
            f"duplicadas removidas: {duplicadas_removidas}, respostas recalculadas: {respostas_recalculadas}, "
            f"sem alteração: {sem_alteracao}."
        )

if __name__ == "__main__":
    with app.app_context():
        importar_csv("GPON.csv", "GPON")
        importar_csv("HFC.csv", "HFC")
