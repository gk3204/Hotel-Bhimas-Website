-- v7g (owner, 13 Oct). Additive and idempotent.

-- (1) "alternate room booking can be done for walk-in or any source with owner approval": a booking the
-- owner approved into an alternate room type at booking time is not asked for approval again at check-in.
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS alternate_approved BOOLEAN NOT NULL DEFAULT FALSE;

-- (3) "getting many off-station issuance fraud for clean card / view card cutting from same station":
-- the desk never sent its station for cleaning / viewing cards, so every one was recorded with none and
-- raised an alert (production: 139 in 10 days). Close those - they were never an off-station issue.
UPDATE fraud_alerts fa
   SET status = 'dismissed',
       reviewed_at = now(),
       review_note = 'v7g: the desk did not send its station for cleaning/viewing cards - not an off-station issue'
  FROM card_issuances ci
 WHERE fa.card_id = ci.id
   AND fa.type = 'off_station_issuance'
   AND fa.status = 'open'
   AND ci.card_type IN ('cleaning', 'viewing')
   AND ci.station_id IS NULL;
