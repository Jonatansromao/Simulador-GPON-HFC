Configuração do ambiente (Windows)

1. Criar e ativar virtualenv (PowerShell):

   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

2. Atualizar pip e instalar dependências:

   python -m pip install --upgrade pip
   pip install -r requirements.txt

3. Executar a aplicação:

   python app.py

4. Notas de segurança:

- Remova `credentials.json` do repositório e adicione-o ao `.gitignore`.
- Considere rotacionar a chave se a `credentials.json` já foi exposta.
- Compartilhe a planilha "Banco_Questoes" com o e-mail do service account em `credentials.json`.
- Crie a aba `Professores` na planilha (o script `check_sheets.py` já pode cri‑á‑la) com cabeçalhos
  `username` e `password_hash`. O primeiro usuário pode ser registrado pela interface.

5. Testes rápidos:

- Abra http://127.0.0.1:5000 no navegador após iniciar o app.
