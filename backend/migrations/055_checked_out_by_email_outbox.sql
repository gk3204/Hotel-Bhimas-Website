-- v7d (owner, 13 Oct). Additive and idempotent.

-- (1) "should also show reception user checked out": who checked the guest out. Nothing stored it;
-- the audit log did (every reception.checkout row has its user), so backfill from there.
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS checked_out_by INTEGER NULL REFERENCES users(user_id);

UPDATE bookings b
   SET checked_out_by = a.user_id
  FROM (SELECT DISTINCT ON (entity_id) entity_id, user_id
          FROM audit_logs
         WHERE action = 'reception.checkout'
           AND entity_type = 'booking' AND user_id IS NOT NULL
         ORDER BY entity_id, created_at DESC) a
 WHERE b.checked_out_by IS NULL
   AND b.status = 'checked_out'
   AND a.entity_id = b.booking_id::text;

-- (3) "automatic resend email for those where mailjet per day limit is over": an e-mail Mailjet
-- refused (daily limit, outage) is kept here and retried by a scheduled sweep instead of being lost.
CREATE TABLE IF NOT EXISTS email_outbox (
    id           SERIAL PRIMARY KEY,
    kind         VARCHAR(40)  NOT NULL,             -- booking_confirmation | report | owner_alert | ...
    to_email     VARCHAR(500) NULL,                 -- for the admin list (the payload has the real list)
    subject      VARCHAR(300) NULL,
    payload      TEXT         NOT NULL,             -- the Mailjet v3.1 request body, as sent
    status       VARCHAR(12)  NOT NULL DEFAULT 'pending',   -- pending | sent | failed
    attempts     INTEGER      NOT NULL DEFAULT 0,
    last_error   VARCHAR(500) NULL,
    created_at   TIMESTAMP    NOT NULL DEFAULT now(),   -- UTC
    next_try_at  TIMESTAMP    NOT NULL DEFAULT now(),
    sent_at      TIMESTAMP    NULL
);
CREATE INDEX IF NOT EXISTS ix_email_outbox_due ON email_outbox (status, next_try_at);
