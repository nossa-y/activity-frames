-- Routine DB schema (per ROUTINE-DB-DESIGN.md).
-- Built ~95% deterministically by build_routine_db.py. The Tier-2 (LLM) columns
-- exist here but are LEFT NULL by the deterministic build; a later idle-time
-- semantic pass fills them. Raw capture stays the source of truth - this DB only
-- POINTS back to it via the evidence table.

PRAGMA foreign_keys = ON;

CREATE TABLE routine (
    id           INTEGER PRIMARY KEY,
    -- deterministic (compiler)
    app          TEXT,
    site         TEXT,
    step_count   INTEGER NOT NULL,
    occurrences  INTEGER NOT NULL,
    first_seen   TEXT,
    last_seen    TEXT,
    -- Tier-2 (LLM) - LEFT NULL by the deterministic build
    name         TEXT,
    description  TEXT,
    confidence   REAL
);

CREATE TABLE step (
    routine_id   INTEGER NOT NULL REFERENCES routine(id),
    ordinal      INTEGER NOT NULL,
    op           TEXT NOT NULL CHECK (op IN ('click','type','navigate')),
    -- multi-modal fingerprint (deterministic; AX from the ui_event, OCR from the elements join)
    ax_role      TEXT,
    ax_name      TEXT,
    ocr_text     TEXT,
    bbox         TEXT,               -- JSON [x,y,w,h] or the captured element_bounds
    -- Tier-2 (LLM) - LEFT NULL by the deterministic build
    is_slot      INTEGER,
    slot_name    TEXT,
    PRIMARY KEY (routine_id, ordinal)
);

-- Pointers back to the raw capture DB (source of truth). No raw data is copied here.
CREATE TABLE evidence (
    routine_id   INTEGER NOT NULL REFERENCES routine(id),
    step_ordinal INTEGER NOT NULL,
    frame_id     INTEGER,
    ui_event_id  INTEGER
);

CREATE INDEX idx_step_routine ON step(routine_id);
CREATE INDEX idx_evidence_routine ON evidence(routine_id);
