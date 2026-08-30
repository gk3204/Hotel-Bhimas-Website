-- Migration: 031_housekeeping_names.sql
-- Description: Role merge + named cleaner/inspector + maintenance lifecycle (v4b8).
-- Purpose:
--   1. supervisor -> housekeeper (DATA change)
--      The owner asked for ONE housekeeping role that also inspects rooms and gives the final
--      approval on maintenance tickets. `supervisor` (F-B) was a web-only oversight login
--      reaching exactly three pages; merging it away removes a role nobody needed as separate.
--
--   2. housekeeping_tasks.cleaned_by_name / inspected_by_name / inspected_at
--      "Cleaned by" and "Inspected by" become picked NAMES from an admin-editable list, so a
--      contract cleaner can be recorded without anyone creating them a login.
--      ⚠️ This is a PARALLEL vocabulary to the existing `cleaned_by`, which is derived at read
--      time from HousekeepingTask.assigned_to -> _staff_name and persisted nowhere but the
--      audit log. Keep both: one answers "which login did the work", the other "whose name
--      goes on the sheet". Do not overwrite one with the other.
--
--   3. maintenance ticket status remap
--      The owner wants the SUPERVISOR's approval to be what marks a ticket resolved, not the
--      technician's own say-so. New flow:
--          open -> assigned -> in_progress | awaiting_parts -> work_done -> resolved
--      The technician's terminal step becomes `work_done`; `resolved` is set only by the
--      verify route. So existing rows shift by one:
--          resolved -> work_done      (the technician said they were done)
--          verified -> resolved       (someone actually signed it off)
--      ⚠️ ORDER MATTERS. resolved->work_done MUST run before verified->resolved, or the rows
--      just renamed to `resolved` would be renamed straight back to `work_done`.
--
-- Database: PostgreSQL
-- Idempotent: YES — the UPDATEs are no-ops once the old values are gone, and the guarded
-- status remap only touches rows still carrying the pre-v4b8 vocabulary.
--
-- NOTE: create_all() does NOT add columns to an existing table, so this file MUST be run.

BEGIN;

-- 1. role merge -------------------------------------------------------------------------
UPDATE users SET role = 'housekeeper' WHERE role = 'supervisor';

-- 2. named cleaner / inspector ------------------------------------------------------------
ALTER TABLE housekeeping_tasks ADD COLUMN IF NOT EXISTS cleaned_by_name VARCHAR(80);
ALTER TABLE housekeeping_tasks ADD COLUMN IF NOT EXISTS inspected_by_name VARCHAR(80);
ALTER TABLE housekeeping_tasks ADD COLUMN IF NOT EXISTS inspected_at TIMESTAMP;

-- 3. maintenance lifecycle ------------------------------------------------------------------
-- Guarded so a re-run cannot shuffle the vocabulary a second time: once no 'verified' rows
-- remain, the remap has already happened and both statements are no-ops.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM maintenance_tickets WHERE status = 'verified') THEN
        UPDATE maintenance_tickets SET status = 'work_done' WHERE status = 'resolved';
        UPDATE maintenance_tickets SET status = 'resolved' WHERE status = 'verified';
    END IF;
END $$;

COMMENT ON COLUMN housekeeping_tasks.cleaned_by_name IS
    'The cleaner''s NAME, picked from the admin-editable `cleaned_by` list (v4b8). Parallel to '
    'the login-derived name from assigned_to — a contract cleaner needs no user account.';
COMMENT ON COLUMN housekeeping_tasks.inspected_by_name IS
    'The inspector''s NAME, picked from the admin-editable `inspected_by` list (v4b8).';

COMMIT;
