"""One-time Google Business Profile bootstrap: get the five GBP_* variables.

    python backend/scripts/gbp_bootstrap.py --client-id <id> --client-secret <secret>

Opens your browser to Google's consent screen (sign in as the profile OWNER or MANAGER),
catches the redirect on localhost, exchanges the code for a REFRESH token, then lists your
accounts and locations and prints the variables with the bare numeric ids the backend
expects (services/gbp_client.py builds .../accounts/{id}/locations/{id}/reviews — NOT the
"accounts/123" form the newer APIs return).

Needs only `requests` (already a backend dependency). Nothing is stored; copy the output.

Before running, in Google Cloud Console:
  1. Enable: "My Business Account Management API", "My Business Business Information API",
     and "Google My Business API" (the legacy v4 one that still owns reviews).
  2. OAuth consent screen -> External -> add the owner's Google account as a Test user ...
     then PUBLISH it. ⚠️ A consent screen left in "Testing" issues refresh tokens that
     EXPIRE AFTER 7 DAYS, and the poller will quietly stop with a token error a week later.
  3. Credentials -> Create OAuth client ID -> type "Desktop app". Copy id + secret.
  4. Apply for Business Profile API access (form-based, free, takes days). Until it is
     approved, the accounts call below returns 403 and the script tells you so.
"""
import argparse
import http.server
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser

import requests

SCOPE = "https://www.googleapis.com/auth/business.manage"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
ACCOUNTS = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
LOCATIONS = "https://mybusinessbusinessinformation.googleapis.com/v1/{account}/locations"
PORT = 8765


class _Catch(http.server.BaseHTTPRequestHandler):
    code = None
    state = None

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("state", [None])[0] != _Catch.state:
            self.send_response(400); self.end_headers(); self.wfile.write(b"state mismatch"); return
        _Catch.code = q.get("code", [None])[0]
        self.send_response(200); self.end_headers()
        self.wfile.write(b"<h2>Done - you can close this tab and return to the terminal.</h2>")

    def log_message(self, *a):  # keep the terminal clean; nothing to log
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    a = ap.parse_args()

    redirect = f"http://localhost:{PORT}/"
    _Catch.state = secrets.token_urlsafe(16)
    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": a.client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "state": _Catch.state,
        # Both are required to get a REFRESH token, not just a one-hour access token.
        "access_type": "offline", "prompt": "consent",
    })
    srv = http.server.HTTPServer(("localhost", PORT), _Catch)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    print("Opening the consent screen... sign in as the Business Profile owner/manager.")
    print("If the browser does not open, paste this:\n  " + url + "\n")
    webbrowser.open(url)
    # handle_request returns after one request; wait up to 5 minutes for it.
    for _ in range(600):
        if _Catch.code: break
        time.sleep(0.5)
    if not _Catch.code:
        print("Timed out waiting for the redirect.", file=sys.stderr); return 2

    r = requests.post(TOKEN, data={
        "code": _Catch.code, "client_id": a.client_id, "client_secret": a.client_secret,
        "redirect_uri": redirect, "grant_type": "authorization_code"}, timeout=30)
    if r.status_code != 200:
        print("Token exchange failed:", r.status_code, r.text[:300], file=sys.stderr); return 2
    tok = r.json()
    refresh = tok.get("refresh_token")
    if not refresh:
        print("Google returned no refresh_token. Revoke the app at myaccount.google.com/permissions "
              "and run again -- a refresh token is only issued on the first consent.", file=sys.stderr)
        return 2
    access = tok["access_token"]
    H = {"Authorization": "Bearer " + access}

    r = requests.get(ACCOUNTS, headers=H, timeout=30)
    if r.status_code == 403:
        print("403 listing accounts: Business Profile API access is not approved for this project yet.",
              file=sys.stderr); return 3
    r.raise_for_status()
    accounts = r.json().get("accounts", [])
    if not accounts:
        print("No Business Profile accounts visible to this Google user.", file=sys.stderr); return 3

    print("\nAccounts:")
    for i, acc in enumerate(accounts):
        print(f"  [{i}] {acc.get('accountName')}  ({acc['name']})")
    acc = accounts[0] if len(accounts) == 1 else accounts[int(input("Pick account #: "))]
    account_id = acc["name"].split("/")[-1]

    r = requests.get(LOCATIONS.format(account=acc["name"]), headers=H,
                     params={"readMask": "name,title,storefrontAddress"}, timeout=30)
    r.raise_for_status()
    locs = r.json().get("locations", [])
    if not locs:
        print("No locations under that account.", file=sys.stderr); return 3
    print("\nLocations:")
    for i, loc in enumerate(locs):
        addr = (loc.get("storefrontAddress") or {}).get("locality", "")
        print(f"  [{i}] {loc.get('title')} {addr}  ({loc['name']})")
    loc = locs[0] if len(locs) == 1 else locs[int(input("Pick location #: "))]
    location_id = loc["name"].split("/")[-1]

    print("\n# ---- Railway variables (bare numeric ids, as gbp_client.py expects) ----")
    print(f"GBP_CLIENT_ID={a.client_id}")
    print(f"GBP_CLIENT_SECRET={a.client_secret}")
    print(f"GBP_REFRESH_TOKEN={refresh}")
    print(f"GBP_ACCOUNT_ID={account_id}")
    print(f"GBP_LOCATION_ID={location_id}")
    print("REVIEW_TEST_INJECT_ENABLED=false     # production: hide the inject-test-review tool")
    print("\nThe refresh token is a long-lived credential for your Google listing. Treat it like a password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
