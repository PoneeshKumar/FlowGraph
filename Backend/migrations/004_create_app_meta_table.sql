-- Migration 004: app_meta — small key/value store for application state that is
-- not payment data (which dataset is loaded, when it was ingested). Written
-- directly, outside the outbox convention, like pipeline_runs.

CREATE TABLE IF NOT EXISTS app_meta (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed the dataset row for the IBM AML HI-Small graph loaded by
-- ml/datasets/run_ingest.py. start/end are the measured TRANSFER.ts bounds.
INSERT INTO app_meta (key, value) VALUES (
    'dataset',
    '{"name": "IBM AML HI-Small", "source": "ibm-hi-small", "labelled": true,
      "start_ts": 1661990400, "end_ts": 1663517880, "rows": null, "uploaded_at": null}'::jsonb
) ON CONFLICT (key) DO NOTHING;
