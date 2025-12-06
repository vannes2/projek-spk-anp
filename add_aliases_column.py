# add_aliases_column.py
import sqlite3, os
DB = os.path.join(os.path.dirname(__file__), "database", "spk_anp.db")
conn = sqlite3.connect(DB)
cur = conn.cursor()
try:
    cur.execute("PRAGMA table_info('criteria');")
    cols = [c[1] for c in cur.fetchall()]
    if 'aliases' not in cols:
        cur.execute("ALTER TABLE criteria ADD COLUMN aliases VARCHAR(500);")
        conn.commit()
        print("Added column aliases.")
    else:
        print("Column aliases already exists.")
except Exception as e:
    print("ERROR:", e)
finally:
    conn.close()
