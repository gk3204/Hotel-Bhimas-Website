# Run pending PMS migrations (003-005, idempotent) on the LOCAL backend DB from backend/.env.
import os, sys
import psycopg2
from dotenv import load_dotenv

BACKEND = r"c:\Users\Hari\OneDrive\Documents\Hotel Bhimas\backend"
load_dotenv(os.path.join(BACKEND, ".env"))

url = os.getenv("DATABASE_URL")
if url:
    conn = psycopg2.connect(url)
    print("Connected via DATABASE_URL: ",url)
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
to_run.append("006_frontdesk_checkin.sql")
to_run.append("007_payments_refunds.sql")
to_run.append("008_rates_agent_pricing.sql")
to_run.append("009_antifraud_controls.sql")
to_run.append("010_cash_shift.sql")
to_run.append("011_housekeeping.sql")
to_run.append("012_customer_crm.sql")
to_run.append("013_whatsapp.sql")
to_run.append("014_reports_dashboard.sql")
to_run.append("015_ota_tracking.sql")
to_run.append("016_compliance_india.sql")
to_run.append("017_desk_payments.sql")
to_run.append("018_google_reviews.sql")
to_run.append("019_backoffice.sql")
to_run.append("020_inventory_complaints.sql")
to_run.append("021_guest_portal.sql")
# 022-024 existed but were never appended here, so `python run_migration.py` silently
# skipped them. All are idempotent, so adding them is safe on an already-migrated DB.
to_run.append("022_booking_guests.sql")
to_run.append("023_linen.sql")
to_run.append("024_room_type_ac.sql")
to_run.append("025_room_service.sql")
to_run.append("026_booking_guest_back_scan.sql")
to_run.append("027_sales_reports_shift_split.sql")
to_run.append("028_room_night_posting.sql")
to_run.append("029_complimentary.sql")
to_run.append("030_alt_room_type.sql")
to_run.append("031_housekeeping_names.sql")
to_run.append("032_booking_occupancy.sql")
to_run.append("033_ota_draft_occupancy.sql")
to_run.append("034_ota_actual_commission.sql")
to_run.append("035_menu_availability_windows.sql")
to_run.append("036_whatsapp_inbox.sql")

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
      "| folio_charges.reversal_of_id:", col_exists("folio_charges", "reversal_of_id"),
      "| owner_otps:", table_exists("owner_otps"),
      "| rooms.status_changed_at:", col_exists("rooms", "status_changed_at"),
      "| app_settings:", table_exists("app_settings"),
      "| cash_shifts.denominations:", col_exists("cash_shifts", "denominations"),
      "| booking_guests.id_scan_back_ref:", col_exists("booking_guests", "id_scan_back_ref"),
      "| folio_charges.menu_item_id:", col_exists("folio_charges", "menu_item_id"))
conn.close()
print("DONE")
