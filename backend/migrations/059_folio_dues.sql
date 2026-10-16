-- v7s (owner, 16 Oct): an agent's guest may leave WITHOUT paying the balance - "pay later" - and pay
-- after a few days. The bill is settled (the room is free, the invoice shows the amount due) and
-- the unpaid amount is tracked as a DUE until the money is received on the web portal.
-- Additive and idempotent.
--
-- due_amount     what was left unpaid when the bill closed (NULL = not a pay-later bill). Never
--                changed afterwards: what is still owed is the bill's live balance.
-- due_cleared_at when the last of it was received.
ALTER TABLE folios ADD COLUMN IF NOT EXISTS due_amount     NUMERIC(10, 2) NULL;
ALTER TABLE folios ADD COLUMN IF NOT EXISTS due_marked_at  TIMESTAMP      NULL;   -- UTC
ALTER TABLE folios ADD COLUMN IF NOT EXISTS due_marked_by  INTEGER        NULL REFERENCES users(user_id);
ALTER TABLE folios ADD COLUMN IF NOT EXISTS due_cleared_at TIMESTAMP      NULL;   -- UTC
CREATE INDEX IF NOT EXISTS ix_folios_due ON folios (due_amount) WHERE due_amount IS NOT NULL;
