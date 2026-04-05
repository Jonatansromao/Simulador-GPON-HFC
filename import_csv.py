import pandas as pd
import os
from app import app
from extensions import db
from models import Questao

def importar_csv(nome_arquivo, banco):
    caminho_arquivo = os.path.join(os.path.dirname(__file__), nome_arquivo)
    df = pd.read_csv(caminho_arquivo)

    with app.app_context():
        for _, row in df.iterrows():
            texto = None if pd.isna(row.get("Pergunta")) else str(row["Pergunta"])
            # Verificar se a questão já existe
            existing = Questao.query.filter_by(texto=texto, banco=banco).first()
            if existing:
                print(f"Questão já existe: {texto[:50]}...")
                continue
            q = Questao(
                texto=texto,
                opcao_a=None if pd.isna(row.get("A")) else str(row["A"]),
                opcao_b=None if pd.isna(row.get("B")) else str(row["B"]),
                opcao_c=None if pd.isna(row.get("C")) else str(row["C"]),
                opcao_d=None if pd.isna(row.get("D")) else str(row["D"]),
                correta=None if pd.isna(row.get("Correta")) else str(row["Correta"]),
                imagem=None if pd.isna(row.get("Imagem")) else str(row["Imagem"]),
                banco=banco
            )
            db.session.add(q)

        db.session.commit()
        print(f"Questões {banco} importadas com sucesso!")

if __name__ == "__main__":
    with app.app_context():
        importar_csv("GPON.csv", "GPON")
        importar_csv("HFC.csv", "HFC")
