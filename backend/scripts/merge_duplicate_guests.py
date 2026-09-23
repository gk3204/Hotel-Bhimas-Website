"""One-off (v5r): merge guest records that are the same person on the same phone number.

Why there are duplicates
------------------------
The desk has always called `match_guest` before creating a guest; the website never did — it ran
`Guest(name=..., phone=..., email=...)` on every booking. And `match_guest` itself compared the raw
string, so `+919876543210` and `9876543210` were two people anyway. Production ended up with 139 guest
rows for 99 real numbers: one repeat customer held eight profiles, each with its own loyalty balance,
its own stay history and its own answer to "is this guest blacklisted?".

v5r fixes both causes. This repairs what they left behind.

What it does, per group of rows sharing a phone (compared on the last 10 digits):
  * keeps the OLDEST guest_id — the one with the longest history and the one older rows point at;
  * fills blanks on the keeper from the others (a name or email the newer row had and the keeper did not);
  * repoints every reference: bookings, guest_profiles, loyalty_ledger, guest_requests,
    guest_portal_sessions, pre_arrival_registrations, whatsapp_messages, google_reviews;
  * merges the PROFILE conservatively: VIP and blacklist survive if any row had them (never lose a
    blacklist), loyalty points are summed, notes are concatenated, and the richest ID/GST details win;
  * deletes the emptied rows and writes one audit entry per merge.

Dry-run by default. Nothing is written until --apply.

    python backend/scripts/merge_duplicate_guests.py                      # local .env, dry run
    python backend/scripts/merge_duplicate_guests.py "<DATABASE_URL>"     # dry run
    python backend/scripts/merge_duplicate_guests.py "<DATABASE_URL>" --apply
"""
import os
import re
import sys
from collections import defaultdict

REFERENCES = [
    ("bookings", "guest_id"),
    ("guest_profiles", "guest_id"),
    ("loyalty_ledger", "guest_id"),
    ("guest_requests", "guest_id"),
    ("guest_portal_sessions", "guest_id"),
    ("pre_arrival_registrations", "guest_id"),
    ("whatsapp_messages", "guest_id"),
    ("google_reviews", "matched_guest_id"),
]


def _resolve_url(argv):
    for a in argv[1:]:
        if not a.startswith("--"):
            return a
    from dotenv import load_dotenv
    backend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    load_dotenv(os.path.join(backend, ".env"))
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    return (f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
            f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}")


def _key(phone):
    d = re.sub(r"\D", "", phone or "")
    return d[-10:] if len(d) >= 10 else d


def main(argv):
    apply_it = "--apply" in argv
    import psycopg2
    conn = psycopg2.connect(_resolve_url(argv))
    cur = conn.cursor()

    cur.execute("SELECT guest_id, name, phone, email FROM guests WHERE phone IS NOT NULL ORDER BY guest_id")
    groups = defaultdict(list)
    for gid, name, phone, email in cur.fetchall():
        k = _key(phone)
        if len(k) >= 10:                 # only merge on a full subscriber number
            groups[k].append((gid, name, phone, email))
    dupes = {k: v for k, v in groups.items() if len(v) > 1}

    total_extra = sum(len(v) - 1 for v in dupes.values())
    print(f"{len(dupes)} number(s) held by more than one guest record; "
          f"{total_extra} row(s) would be merged away"
          f"{'' if apply_it else '   (DRY RUN — nothing written)'}")
    for k, rows in sorted(dupes.items(), key=lambda x: -len(x[1]))[:20]:
        keeper = rows[0]
        print(f"  …{k}: keep #{keeper[0]} ({keeper[1]}) + merge "
              + ", ".join(f"#{g}" for g, *_ in rows[1:]))
    if len(dupes) > 20:
        print(f"  … and {len(dupes) - 20} more")
    if not dupes:
        conn.close()
        return 0
    if not apply_it:
        print("\nRe-run with --apply to merge.")
        conn.close()
        return 0

    merged = 0
    for k, rows in dupes.items():
        keeper_id = rows[0][0]
        loser_ids = [g for g, *_ in rows[1:]]

        # Fill blanks on the keeper from the newer rows (they hold the most recent spelling).
        for gid, name, phone, email in reversed(rows[1:]):
            cur.execute("""UPDATE guests SET name = COALESCE(NULLIF(name, ''), %s),
                                             email = COALESCE(NULLIF(email, ''), %s)
                            WHERE guest_id = %s""", (name, email, keeper_id))

        # Merge the profile before repointing, so two profile rows never collide on guest_id.
        cur.execute("""SELECT guest_id, vip, blacklist, blacklist_reason, loyalty_points, notes,
                              id_type, id_number_masked, address, gstin, gst_legal_name
                         FROM guest_profiles WHERE guest_id = ANY(%s) ORDER BY guest_id""",
                    ([keeper_id] + loser_ids,))
        profs = cur.fetchall()
        if profs:
            vip = any(p[1] for p in profs)
            black = any(p[2] for p in profs)
            reason = next((p[3] for p in profs if p[2] and p[3]), None)
            points = sum(int(p[4] or 0) for p in profs)
            notes = " | ".join(sorted({(p[5] or "").strip() for p in profs if (p[5] or "").strip()}))
            pick = lambda i: next((p[i] for p in profs if p[i]), None)   # noqa: E731
            if any(p[0] == keeper_id for p in profs):
                cur.execute("""UPDATE guest_profiles
                                  SET vip=%s, blacklist=%s, blacklist_reason=COALESCE(%s, blacklist_reason),
                                      loyalty_points=%s, notes=NULLIF(%s,''),
                                      id_type=COALESCE(id_type,%s), id_number_masked=COALESCE(id_number_masked,%s),
                                      address=COALESCE(address,%s), gstin=COALESCE(gstin,%s),
                                      gst_legal_name=COALESCE(gst_legal_name,%s)
                                WHERE guest_id=%s""",
                            (vip, black, reason, points, notes, pick(6), pick(7), pick(8), pick(9),
                             pick(10), keeper_id))
                cur.execute("DELETE FROM guest_profiles WHERE guest_id = ANY(%s)", (loser_ids,))
            else:
                # No profile on the keeper: promote the first loser's row, drop the rest.
                promote = profs[0][0]
                cur.execute("""UPDATE guest_profiles
                                  SET guest_id=%s, vip=%s, blacklist=%s, loyalty_points=%s, notes=NULLIF(%s,'')
                                WHERE guest_id=%s""", (keeper_id, vip, black, points, notes, promote))
                cur.execute("DELETE FROM guest_profiles WHERE guest_id = ANY(%s)",
                            ([g for g in loser_ids if g != promote],))

        for table, col in REFERENCES:
            if table == "guest_profiles":
                continue
            cur.execute(f"UPDATE {table} SET {col} = %s WHERE {col} = ANY(%s)", (keeper_id, loser_ids))

        cur.execute("""INSERT INTO audit_logs (user_id, action, entity_type, entity_id, before, after,
                                               client, created_at)
                       VALUES (NULL, 'guest.merged', 'guest', %s, %s, %s, 'script', now())""",
                    (str(keeper_id),
                     '{"merged_guest_ids": [%s]}' % ", ".join(str(g) for g in loser_ids),
                     '{"kept": %d, "reason": "v5r: one phone number, several guest records"}' % keeper_id))
        cur.execute("DELETE FROM guests WHERE guest_id = ANY(%s)", (loser_ids,))
        merged += len(loser_ids)

    conn.commit()
    cur.execute("SELECT count(*) FROM guests")
    remaining = cur.fetchone()[0]
    print(f"\nMerged {merged} duplicate row(s); {remaining} guest record(s) remain.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
