-- Validate only the current native Dendrite time window. UTC timestamps are
-- normalized by the ledger on insertion. No causal rows or identities change.
CREATE INDEX IF NOT EXISTS idx_causal_events_world_subject_queue_time
    ON causal_events(world_id, engram_id, status, created_at, seq);

-- Keep each discovery sweep within its own World and a finite queue horizon.
CREATE INDEX IF NOT EXISTS idx_causal_events_world_status_seq
    ON causal_events(world_id, status, seq);
