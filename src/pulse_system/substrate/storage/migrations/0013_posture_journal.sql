-- Durable experience and its revisions share the existing World database.
-- Legacy component JSON is imported atomically on the first ordinary mutation.
CREATE TABLE IF NOT EXISTS posture_journals (
    world_id TEXT PRIMARY KEY,
    format_version INTEGER NOT NULL CHECK (format_version = 1)
);
CREATE TABLE IF NOT EXISTS posture_experiences (
    world_id TEXT NOT NULL,
    experience_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('context', 'subject')),
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    updated_at TEXT NOT NULL,
    content_updated_at TEXT NOT NULL,
    record TEXT NOT NULL,
    PRIMARY KEY (world_id, experience_id)
);
CREATE INDEX IF NOT EXISTS idx_posture_subject
    ON posture_experiences (world_id, subject_id, active, updated_at DESC, experience_id);
CREATE TABLE IF NOT EXISTS posture_experience_revisions (
    world_id TEXT NOT NULL,
    experience_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    record TEXT NOT NULL,
    feedback_count INTEGER NOT NULL CHECK (feedback_count >= 0),
    PRIMARY KEY (world_id, experience_id, revision),
    FOREIGN KEY (world_id, experience_id)
        REFERENCES posture_experiences (world_id, experience_id)
);
CREATE TABLE IF NOT EXISTS posture_experience_feedback (
    world_id TEXT NOT NULL,
    experience_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    record TEXT NOT NULL,
    PRIMARY KEY (world_id, experience_id, position),
    FOREIGN KEY (world_id, experience_id)
        REFERENCES posture_experiences (world_id, experience_id)
);
