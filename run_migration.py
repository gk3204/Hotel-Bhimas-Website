# Run pending PMS migrations (003-005, idempotent) on the LOCAL backend DB from backend/.env.
import os, sys
import psycopg2
from dotenv import load_dotenv

BACKEND = r"c:\Users\Hari\OneDrive\Documents\Hotel Bhimas\backend"
load_dotenv(os.path.join(BACKEND, ".env"))

url = os.getenv("DATABASE_URL")
if url:
    conn = psycopg2.connect(url)
    print("Connected via DATABASE_URL")
else:
    host, port = os.getenv("DB_HOST"), os.getenv("DB_PORT")
    name = os.getenv("DB_NAME")
    conn = psycopg2.connect(host=host, port=port, user=os.getenv("DB_USER"),
                            password=os.getenv("DB_PASSWORD"), dbname=name, connect_timeout=5)
    print(f"Connected to {host}:{port}/{name}")

cur = conn.cursor()

def table_exists(t):
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name=%s", (t,))
    return cur.fetchone() is not None

def col_exists(t, c):
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s", (t, c))
    return cur.fetchone() is not None

print("Before: folios:", table_exists("folios"),
      "| rooms.is_active:", col_exists("rooms", "is_active"),
      "| invoices:", table_exists("invoices"),
      "| folio_charges.reversal_of_id:", col_exists("folio_charges", "reversal_of_id"))

# 005 needs folios (003); 005 alone if 003/004 already applied. All are idempotent.
to_run = []
if not table_exists("folios"):
    to_run.append("003_pms_foundation.sql")
if not col_exists("rooms", "is_active"):
    to_run.append("004_rooms_setup.sql")
to_run.append("005_billing_folio.sql")

for f in to_run:
    path = os.path.join(BACKEND, "migrations", f)
    with open(path, encoding="utf-8") as fh:
        sql = fh.read()
    print(f"-- applying {f} ...", end=" ")
    cur.execute(sql)
    conn.commit()
    print("OK")

print("After: folios:", table_exists("folios"),
      "| invoices:", table_exists("invoices"),
      "| invoice_counters:", table_exists("invoice_counters"),
      "| folio_charges.reversal_of_id:", col_exists("folio_charges", "reversal_of_id"))
conn.close()
print("DONE")
