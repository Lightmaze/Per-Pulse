-- Small attributed intentions belong to the existing World, independently
-- of whether their source turn eventually completes or is cancelled.
CREATE TABLE IF NOT EXISTS local_notices (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL,
    engram_id TEXT NOT NULL REFERENCES engrams(id),
    context_id TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'closed')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    source_turn_id TEXT NOT NULL REFERENCES harness_turns(id),
    source_event_id TEXT NOT NULL REFERENCES causal_events(id),
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    close_source TEXT,
    CHECK((status = 'pending' AND closed_at IS NULL AND close_source IS NULL)
       OR (status = 'closed' AND closed_at IS NOT NULL AND close_source IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_local_notices_context
    ON local_notices(world_id, context_id, engram_id, status, created_at, id);
