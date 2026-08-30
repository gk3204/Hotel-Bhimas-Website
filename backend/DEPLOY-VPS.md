# Deploying the Hotel Bhimas backend on a VPS

Self-host the backend + PostgreSQL on a single Linux VPS, with automatic HTTPS. This replaces
Railway; the desktop app, the website, and the guest QR portal all point at the same public URL.

**What you run:** three containers via `docker compose` — `api` (this FastAPI app), `db`
(PostgreSQL), `caddy` (TLS + reverse proxy). Postgres is private to the compose network; only 80/443
are open to the world.

**What is now YOUR job (Railway did these for free):** backups, uptime, OS patching, and the TLS is
automated by Caddy. Do §9 (backups) and §11 (monitoring) — a VPS without them is a false economy.

> Companion docs: the go-live runbook (`HotelLock/docs/go-live-and-desk-migration.md`) still governs
> the desk PC, encoder DLLs, env meanings (§B2), and the ID-scan safeguards (§B6). This file only
> changes *where the backend runs*.

---

## 0. Before you start
- A **domain** you control (e.g. `hotelbhimas.in`) so you can add a subdomain for the API.
- The **backup dump** of your current Railway database if you want to carry the live data over (§7B).
- Choose a provider by **region + price** — the deploy below is identical on any of them. For a
  Tirupati hotel, prefer an **India/Singapore region**: DigitalOcean **Bangalore (BLR1)**, a Mumbai
  region (AWS `ap-south-1`), or Hetzner (Singapore). Cheap "bare VPS" providers (Hetzner, DigitalOcean,
  Vultr, Linode, Contabo) are the right tier; avoid the hyperscalers' complexity for one hotel.
- **Size:** Ubuntu 24.04 LTS, **2 vCPU / 2 GB RAM**, 40 GB disk. (1 GB works only with `--workers 1`.)

---

## 1. Create the server
Create an **Ubuntu 24.04** instance, add your SSH public key during creation, note its **public IP**.
If the provider has a cloud firewall, allow **22, 80, 443** inbound and deny the rest (you'll also set
`ufw` in §3 as belt-and-suspenders).

SSH in:
```bash
ssh root@<VPS_IP>
```

---

## 2. Point DNS at the box
At your DNS host (Cloudflare is a good free choice), add an **A record**:

```
api.hotelbhimas.in   A   <VPS_IP>
```

Wait for it to resolve (`ping api.hotelbhimas.in` shows the IP). Caddy needs this working to issue the
certificate. If you use Cloudflare, set this record to **DNS only (grey cloud)** for the first cert
issuance, or use Cloudflare's Full(strict) mode.

---

## 3. Harden the box (10 minutes, do not skip)
```bash
# a non-root sudo user for day-to-day
adduser deploy && usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy   # copy your key so you can SSH as deploy

# firewall
apt update && apt install -y ufw fail2ban unattended-upgrades
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
systemctl enable --now fail2ban
dpkg-reconfigure -plow unattended-upgrades   # accept auto security updates
```
Then log in again as `deploy` and disable root/password SSH in `/etc/ssh/sshd_config`
(`PermitRootLogin no`, `PasswordAuthentication no`) → `sudo systemctl restart ssh`.

---

## 4. Install Docker + Compose
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER      # log out/in once so `docker` works without sudo
docker --version && docker compose version
```

---

## 5. Get the code onto the server
Clone your private repo (or `scp` the `backend/` folder up). You only need the **backend** directory.
```bash
git clone https://github.com/<you>/<repo>.git
cd <repo>/backend        # the folder that has Dockerfile + docker-compose.yml
```

---

## 6. Configure
```bash
cp .env.vps.example .env
nano .env                # fill EVERY CHANGE_ME — see the runbook §B2 for what each means
```
Generate the two secrets it asks for:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"                       # SECRET_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # ID_SCAN_ENCRYPTION_KEY
```
Keep `POSTGRES_*` and the password inside `DATABASE_URL` **identical**.

Edit the domain + email in the proxy config:
```bash
nano Caddyfile           # replace api.hotelbhimas.in and the email address
```

Create the persistent ID-scan folder, owned by the container's user (uid 1000):
```bash
mkdir -p data/id_scans && sudo chown -R 1000:1000 data/id_scans
```

---

## 7. Bring it up + set up the database

```bash
docker compose up -d --build
docker compose ps                 # api, db, caddy all "running"/"healthy"
```

Now the database schema. **Pick ONE path:**

### 7A. Fresh database (no existing data to keep)
The app calls `create_all()` on startup, so tables are created automatically. Run the numbered
migrations too (they're `IF NOT EXISTS`, so they just add anything create_all doesn't) and create the
first admin:
```bash
# migrations, in order. The single-quoted `sh -c` makes $POSTGRES_* resolve INSIDE the db container
# (where they exist), so you don't have to export anything on the host.
for f in $(ls migrations/*.sql | sort); do
  echo "== $f =="
  docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < "$f" || break
done

# first admin (there is no seeded one) — change the password immediately after logging in
docker compose exec api python -c "from database import SessionLocal; from models import User; from routers.users import hash_password; db=SessionLocal(); db.add(User(username='admin', password_hash=hash_password('CHANGE_ME_admin_pw'), role='admin')); db.commit(); print('admin created')"
```

### 7B. Migrate your live Railway data (recommended — you keep bookings + your admin account)
On your PC, dump Railway; copy the dump to the VPS; restore into the container; then run migrations:
```bash
# on your PC
pg_dump "<RAILWAY_DATABASE_URL>" --format=custom --no-owner --no-privileges -f bhimas.dump
scp bhimas.dump deploy@<VPS_IP>:~/

# on the VPS (in the backend folder)
docker compose cp ~/bhimas.dump db:/tmp/bhimas.dump
docker compose exec db sh -c 'pg_restore --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB" /tmp/bhimas.dump'
for f in $(ls migrations/*.sql | sort); do docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < "$f" || break; done
```
Your existing admin login comes across with the data — no need to seed one.
**Also copy the ID-scan files** from wherever they live now into `data/id_scans/` (the DB dump does
NOT contain them), or `has_scan: true` rows will point at missing files.

---

## 8. Verify
```bash
curl -s https://api.hotelbhimas.in/docs -o /dev/null -w "%{http_code}\n"   # 200
docker compose logs api | grep -iE "scheduler|CORS"                        # scheduler lines appear
docker compose exec api date                                               # must read IST, not UTC
```
- The HTTPS padlock works (Caddy issued the cert automatically). If not, re-check the DNS A record and
  `docker compose logs caddy`.
- Log in as admin from the website; create the real staff accounts (Admin → User Management, one per
  person) per runbook §B5.

---

## 9. Point everything at the new URL
- **Desktop** `C:\HotelBhimas\ReceptionApp\appsettings.json` → `"ApiBaseUrl": "https://api.hotelbhimas.in"`, restart the app.
- **Website (Vercel)** → `VITE_API_URL = https://api.hotelbhimas.in`, redeploy.
- **Razorpay dashboard** → webhook URL `https://api.hotelbhimas.in/payments/webhook` (events:
  payment.captured, payment.failed, qr_code.credited, payment_link.paid); its secret must equal
  `RAZORPAY_WEBHOOK_SECRET`.
- **Meta WhatsApp** → Configuration → Callback URL `https://api.hotelbhimas.in/whatsapp/webhook`, verify
  token = `WHATSAPP_VERIFY_TOKEN`.

---

## 10. Backups — the part you now own
The database and the ID scans are only as safe as your backups. Add a nightly job:
```bash
sudo tee /usr/local/bin/bhimas-backup.sh >/dev/null <<'SH'
#!/usr/bin/env bash
set -e
cd /home/deploy/<repo>/backend
mkdir -p /home/deploy/backups
STAMP=$(date +%F)
# database ($POSTGRES_* resolve inside the container via the quoted sh -c)
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > /home/deploy/backups/db-$STAMP.dump
# encrypted ID scans
tar czf /home/deploy/backups/idscans-$STAMP.tar.gz -C data id_scans
# keep 30 days
find /home/deploy/backups -type f -mtime +30 -delete
SH
sudo chmod +x /usr/local/bin/bhimas-backup.sh
( crontab -l 2>/dev/null; echo "0 3 * * * /usr/local/bin/bhimas-backup.sh" ) | crontab -
```
- **Copy `/home/deploy/backups` OFF the VPS** (rclone to R2/Google Drive, or a scheduled `scp` to
  another machine). A backup that lives only on the box it protects is not a backup.
- **Do a restore drill once** into a scratch DB, and confirm it opens. A dump you've never restored is
  a rumour.
- **Escrow `ID_SCAN_ENCRYPTION_KEY`** offline — it is NOT in the dump; lose it and every scan is
  permanently unreadable.

---

## 11. Monitoring & uptime
- `restart: unless-stopped` (already set) brings containers back after a crash or reboot.
- Put the VPS on a **UPS** if the "server" is on-site — guest room-service ordering depends on it.
- Free external check: **UptimeRobot** hitting `https://api.hotelbhimas.in/docs` every 5 min → alerts
  you if it goes down.
- Weekly: `docker compose ps`, `df -h` (disk), `free -m` (memory), `docker compose logs --since 24h api | grep -i error`.

---

## 12. Deploying an update later
```bash
cd <repo>/backend
git pull
docker compose up -d --build            # rebuilds api, keeps db + volumes
# run any NEW migrations added since last deploy (idempotent, safe to re-run all):
for f in $(ls migrations/*.sql | sort); do docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < "$f" || break; done
```
The `pgdata` and `data/id_scans` volumes survive rebuilds — only the `api` image is replaced.

---

## 13. Troubleshooting
| Symptom | Likely cause / fix |
|---|---|
| No HTTPS / cert error | DNS A record not resolving yet, or Cloudflare proxy on during first issue. `docker compose logs caddy` |
| `api` restarts / can't reach DB | `DATABASE_URL` host must be `db` and password must match `POSTGRES_PASSWORD`; check `docker compose logs api` |
| ID scans fail with 503 | `ID_SCAN_ENCRYPTION_KEY` not set |
| Scans vanish after `up --build` | `data/id_scans` not mounted or wrong ownership — `sudo chown -R 1000:1000 data/id_scans` |
| Times off by 5.5h | `TZ=Asia/Kolkata` missing (env + compose already set it) |
| Website calls blocked (CORS) | add the site domains to `CORS_ORIGINS`, `docker compose up -d` |
| UPI/payment never confirms | `RAZORPAY_WEBHOOK_SECRET` wrong or webhook URL not updated to the new domain |

---

## 14. Cost
One 2 GB VPS (~$5–7/mo) runs the whole stack — backend (2 workers), Postgres, and Caddy. Backups to
object storage are a few cents. That's the trade for owning uptime + backups yourself; Railway's ~$20
mostly buys not having to. Keep §10 and §11 honest and this is a solid, low-cost home for it.
