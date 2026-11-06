-- Exact consultation provenance must not scan completed outbox history.
-- Older malformed component JSON cannot prevent this additive migration;
-- only verified fields can match an origin lookup. No stored state changes.
CREATE INDEX IF NOT EXISTS idx_native_team_origin
    ON component_state (
        CASE WHEN json_valid(state) THEN json_extract(state, '$.subject') END,
        CASE WHEN json_valid(state) THEN json_extract(state, '$.delegation_event_id') END,
        updated_at DESC,
        component
    )
    WHERE component GLOB 'native-team-v1:*';
