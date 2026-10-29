-- Keep native local-context observation and admission proportional to the
-- matching evidence, rather than rescanning every historical turn/event.
-- This migration adds only indexes; all causal rows retain their identities.

CREATE INDEX IF NOT EXISTS idx_harness_turns_result_event
    ON harness_turns(result_event_id)
    WHERE result_event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_causal_events_world_kind_status_seq
    ON causal_events(world_id, kind, status, seq);

-- Match the rolling admission predicate exactly. julianday(column) preserves
-- timezone-offset equivalence and permits an indexed lower time boundary.
CREATE INDEX IF NOT EXISTS idx_context_pulse_budget
    ON causal_events(world_id, julianday(created_at))
    WHERE kind IN ('spontaneous', 'propagation')
      AND (json_extract(metadata, '$.context_pulse') = 'reentry'
           OR json_extract(metadata, '$.context_connection_id') IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_context_pulse_recent
    ON causal_events(world_id, seq)
    WHERE kind IN ('spontaneous', 'propagation', 'pulse')
      AND (json_extract(metadata, '$.context_pulse') IN ('reentry', 'convergence')
           OR json_extract(metadata, '$.context_connection_id') IS NOT NULL);
