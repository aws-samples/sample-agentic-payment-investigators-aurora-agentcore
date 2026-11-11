-- ============================================================================
-- indexes_postload.sql — HNSW builds, run AFTER seed COPY (see load order in schema.sql)
--
-- Measured: default maintenance_work_mem overflows at ~55k
-- 1024-dim tuples ("hnsw graph no longer fits...") and 60k took 116 s on
-- 2–8 ACU Sv2. Raise mwm in-session; total corpus is precedents + rebuttals +
-- clauses + notes ≈ 110k+ vectors across 4 indexes.
-- ============================================================================
SET maintenance_work_mem = '2GB';

-- --reset reseeds must not leave a stale HNSW graph (built over prior data):
DROP INDEX IF EXISTS precedent_complaint_hnsw, precedent_rebuttal_hnsw,
                     clause_hnsw, note_hnsw, statement_narration_trgm;

CREATE INDEX IF NOT EXISTS precedent_complaint_hnsw ON precedent_cases
  USING hnsw (complaint_embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS precedent_rebuttal_hnsw ON precedent_cases
  USING hnsw (rebuttal_embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS clause_hnsw ON rule_clauses
  USING hnsw (clause_embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS note_hnsw ON break_resolutions
  USING hnsw (note_embedding vector_cosine_ops);

-- pg_trgm GIN for the pre-built deterministic matcher's stage-3 (recon)
CREATE INDEX IF NOT EXISTS statement_narration_trgm ON statement_lines
  USING gin (lower(narration) gin_trgm_ops);

ANALYZE;

-- HNSW recall floor (hnsw.ef_search = 200): the default (40) walked a
-- parallel-built graph on a live event floor and MISSED the query's own
-- vector (distance zero) — per-build nondeterminism makes recall a floor
-- lottery at defaults; 200 measured-heals and makes ANN effectively exact
-- here (live event, 2026-08-20). NOT settable in this file:
-- Aurora denies ALTER DATABASE/ROLE SET for extension GUCs even to the
-- master user ("permission denied to set parameter", measured). The floor
-- default ships as PGOPTIONS='-c hnsw.ef_search=200' instead — see
-- bootstrap_webhost.sh (shells + dev loop), wire_env.sh (deployed
-- runtime), runtime_manager.py (incumbent runtime).

-- ⚠ Deliberately ABSENT: CREATE INDEX ON precedent_cases (reason_code, outcome).
-- The facilitator creates it live in M2's planner beat (see schema.sql header).
