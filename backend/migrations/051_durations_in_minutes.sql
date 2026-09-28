-- 051: every duration setting is stored in MINUTES.
--
-- Why
-- ---
-- The owner asked for it, and the reason is sound: minutes can express an hour but hours cannot
-- express ten minutes. Several of these were whole-HOUR integers server-side (`_as_int`), so a
-- half-hour was not merely awkward to type, it was unrepresentable — "alert me if a room sits
-- dirty for 30 minutes" could not be said at all. The ones that did take fractions only moved in
-- half-hour steps in the admin form.
--
-- There is already a precedent for the unit: `fraud_cleaning_min_minutes` has always been in
-- minutes, because that check needed sub-hour precision. This brings the rest into line so the
-- settings screen has ONE unit rather than two.
--
-- What it does
-- ------------
-- Renames each `*_hours` duration key to `*_minutes` and multiplies its value by 60. Idempotent:
-- it only acts when the old key is present and the new one is not, so re-running is a no-op and a
-- database that never had the old key is untouched.
--
-- ⚠️ NOT converted, because they are not durations:
--   * `fraud_allowed_issue_hours` — a clock RANGE ("06-23"), the window cards may be cut in.
--   * `wa_daily_digest_hour`      — an hour OF THE DAY (0-23), when the digest is sent.
-- Multiplying either by 60 would be nonsense, so they keep their names and their meaning.

BEGIN;

DO $$
DECLARE
    pair   RECORD;
    oldval TEXT;
BEGIN
    FOR pair IN
        SELECT * FROM (VALUES
            ('crm_registration_link_ttl_hours',   'crm_registration_link_ttl_minutes'),
            ('wa_checkout_reminder_lead_hours',   'wa_checkout_reminder_lead_minutes'),
            ('wa_review_delay_hours',             'wa_review_delay_minutes'),
            ('review_post_delay_min_hours',       'review_post_delay_min_minutes'),
            ('review_post_delay_max_hours',       'review_post_delay_max_minutes'),
            ('attendance_auto_close_hours',       'attendance_auto_close_minutes'),
            ('fraud_cleaning_max_hours',          'fraud_cleaning_max_minutes'),
            ('fraud_inspection_max_hours',        'fraud_inspection_max_minutes'),
            ('complaint_sla_response_hours_urgent', 'complaint_sla_response_minutes_urgent'),
            ('complaint_sla_response_hours_high',   'complaint_sla_response_minutes_high'),
            ('complaint_sla_response_hours_normal', 'complaint_sla_response_minutes_normal'),
            ('complaint_sla_response_hours_low',    'complaint_sla_response_minutes_low'),
            ('complaint_sla_resolve_hours_urgent',  'complaint_sla_resolve_minutes_urgent'),
            ('complaint_sla_resolve_hours_high',    'complaint_sla_resolve_minutes_high'),
            ('complaint_sla_resolve_hours_normal',  'complaint_sla_resolve_minutes_normal'),
            ('complaint_sla_resolve_hours_low',     'complaint_sla_resolve_minutes_low')
        ) AS t(old_key, new_key)
    LOOP
        -- Only when the old key exists and the new one does not: re-running must change nothing,
        -- and a value an admin has already set in minutes must never be multiplied a second time.
        IF EXISTS (SELECT 1 FROM app_settings WHERE key = pair.old_key)
           AND NOT EXISTS (SELECT 1 FROM app_settings WHERE key = pair.new_key) THEN

            SELECT value INTO oldval FROM app_settings WHERE key = pair.old_key;

            -- The stored text may be a fraction ("0.5" = 30 minutes) or junk. A value that is not
            -- a number is dropped rather than converted — the accessor then falls back to its
            -- default, which is the same thing it did with the unreadable value before.
            IF oldval ~ '^\s*[0-9]+(\.[0-9]+)?\s*$' THEN
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (pair.new_key,
                        trim(to_char(round((oldval::numeric) * 60), 'FM9999999990')),
                        NOW());
            END IF;

            DELETE FROM app_settings WHERE key = pair.old_key;
        END IF;
    END LOOP;
END $$;

COMMIT;
