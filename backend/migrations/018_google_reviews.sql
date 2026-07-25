-- Migration: 018_google_reviews.sql
-- Description: Google review auto-reply / reputation management (prompt 20).
-- Purpose:
--   1. google_reviews -> new table: one row per Google Business Profile review + the reply we
--                        generate/post. Deduped on gbp_review_id. >=4 star = auto thank-you (after a
--                        randomized delay); <4 star = softened draft in the admin approval queue + owner
--                        alert + auto-created complaint ticket.
--   2. app_settings   -> seed review-automation config (threshold, delay window, per-band auto-send,
--                        LLM on/off, poll cadence, owner alerts). Never overwrites an admin edit.
-- The table is ALSO auto-created by SQLAlchemy create_all; included here for prod parity via psql.
-- Database: PostgreSQL
-- Idempotent: YES (IF NOT EXISTS + ON CONFLICT DO NOTHING)

BEGIN;

-- ============================================
-- 1. google_reviews (new table)
-- ============================================
CREATE TABLE IF NOT EXISTS google_reviews (
    id                 SERIAL PRIMARY KEY,
    gbp_review_id      VARCHAR(200) NOT NULL UNIQUE,
    rating             INTEGER NOT NULL,
    author_name        VARCHAR(150),
    review_text        TEXT,
    review_created_at  TIMESTAMP,
    matched_guest_id   INTEGER REFERENCES guests(guest_id),
    reply_text         TEXT,
    reply_mode         VARCHAR(20),
    reply_status       VARCHAR(30) NOT NULL DEFAULT 'pending_generation',
    scheduled_post_at  TIMESTAMP,
    replied_at         TIMESTAMP,
    ticket_id          INTEGER REFERENCES maintenance_tickets(id),
    last_error         VARCHAR(500),
    retry_count        INTEGER NOT NULL DEFAULT 0,
    created_at         TIMESTAMP DEFAULT now(),
    updated_at         TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_google_reviews_gbp_id        ON google_reviews(gbp_review_id);
CREATE INDEX IF NOT EXISTS idx_google_reviews_rating        ON google_reviews(rating);
CREATE INDEX IF NOT EXISTS idx_google_reviews_status        ON google_reviews(reply_status);
CREATE INDEX IF NOT EXISTS idx_google_reviews_scheduled     ON google_reviews(scheduled_post_at);
CREATE INDEX IF NOT EXISTS idx_google_reviews_created_at    ON google_reviews(created_at);
CREATE INDEX IF NOT EXISTS idx_google_reviews_review_at     ON google_reviews(review_created_at);
CREATE INDEX IF NOT EXISTS idx_google_reviews_matched_guest ON google_reviews(matched_guest_id);

-- ============================================
-- 2. app_settings: review-automation config seeds (never overwrites an admin edit)
--    Auto-post thank-you for rating >= threshold (4). Low ratings (<4) go to the approval queue
--    by default (review_low_auto_send=false). LLM generation off by default (needs ANTHROPIC_API_KEY too).
-- ============================================
INSERT INTO app_settings (key, value) VALUES
    ('review_auto_reply_enabled',    'true'),
    ('review_auto_reply_threshold',  '4'),
    ('review_low_band_split',        '2'),
    ('review_post_delay_min_hours',  '2'),
    ('review_post_delay_max_hours',  '6'),
    ('review_low_auto_send',         'false'),
    ('review_llm_enabled',           'false'),
    ('review_poll_interval_minutes', '15'),
    ('review_owner_alerts_enabled',  'true')
ON CONFLICT (key) DO NOTHING;

COMMIT;

-- ============================================
-- VERIFICATION (run manually if desired)
-- ============================================
-- SELECT column_name FROM information_schema.columns WHERE table_name='google_reviews' ORDER BY ordinal_position;
-- SELECT key, value FROM app_settings WHERE key LIKE 'review_%' ORDER BY key;
