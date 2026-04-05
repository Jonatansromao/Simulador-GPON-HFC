import sqlite3
for path in ['simulador.db', 'instance/simulador.db', 'instance/simulador_etn.db']:
    try:
        conn = sqlite3.connect(path)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in c.fetchall()]
        print(f"DB: {path}")
        print("Tables:", tables)
        if 'professores' in tables:
            c.execute('PRAGMA table_info(professores)')
            print('professores cols:', [row[1] for row in c.fetchall()])
        conn.close()
    except Exception as e:
        print(f"Erro em {path}: {e}")
