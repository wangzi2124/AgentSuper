import sqlite3
db = 'data/session.db'
conn = sqlite3.connect(db)
rows = conn.execute("SELECT sql FROM sqlite_master WHERE sql LIKE '%REFERENCES%'").fetchall()
for r in rows:
    print('--', r[0][:400], '\n')
try:
    print('projects:', conn.execute('SELECT id, name FROM projects LIMIT 10').fetchall())
except Exception as e:
    print('projects err', e)
print('foreign_keys enopragma:', conn.execute('PRAGMA foreign_keys').fetchone())
conn.close()