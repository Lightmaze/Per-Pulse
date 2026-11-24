-- Read a bounded recent or older conversation without sorting another
-- context's history. Native World/Center/lineage predicates remain in the
-- projection query; no causal rows, receipts or identities are changed.
CREATE INDEX IF NOT EXISTS idx_causal_events_world_center_seq
    ON causal_events(world_id, center_id, seq);
