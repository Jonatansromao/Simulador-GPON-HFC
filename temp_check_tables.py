import sqlite3
conn = sqlite3.connect('instance/simulador_etn.db')
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
print([row[0] for row in c.fetchall()])
conn.close()
