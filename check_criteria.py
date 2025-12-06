# check_criteria.py
import sqlite3, os, sys

DB = os.path.join(os.path.dirname(__file__), "database", "spk_anp.db")
print("DB path:", os.path.abspath(DB))

if not os.path.exists(DB):
    print("ERROR: database file NOT FOUND")
    sys.exit(1)

try:
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    print("\n=== TABLE STRUCTURE ===")
    cur.execute("PRAGMA table_info('criteria');")
    schema = cur.fetchall()
    print(schema if schema else "NO TABLE FOUND")

    print("\n=== ROWS ===")
    cur.execute("SELECT id, name, description, weight_default FROM criteria;")
    rows = cur.fetchall()
    print("Total rows:", len(rows))
    for row in rows:
        print(" ", row)

    conn.close()

except Exception as e:
    print("ERROR:", e)
