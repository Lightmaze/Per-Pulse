-- Make native Harness turn admission context-scoped without rewriting history.
-- Store._init_schema adds execution_context with the legacy-safe empty default
-- before ordered migrations run. Existing running turns therefore remain in
-- the default context and retain their identity.
DROP INDEX IF EXISTS idx_harness_turns_one_running_engram;

CREATE UNIQUE INDEX IF NOT EXISTS idx_harness_turns_one_running_context
    ON harness_turns(engram_id, execution_context)
    WHERE state = 'running';
