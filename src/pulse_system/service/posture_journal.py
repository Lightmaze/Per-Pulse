"""Row-based persistence for the existing World's attributed experience.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The public experience shape stays unchanged. Feedback is stored once, while
each immutable revision refers to the prefix that existed at that time.
Legacy component JSON remains untouched and ceases to be authoritative only
after a complete import and the requested mutation commit together.
"""
from __future__ import annotations

from copy import deepcopy
import json


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def read_experience_record(conn, world_id, identity, *, history=True):
    """Reconstruct one native row record without writing or migrating a database."""
    row = conn.execute("SELECT record,content_updated_at FROM posture_experiences WHERE world_id=? AND experience_id=?",
                       (world_id, identity)).fetchone()
    if row is None:
        raise ValueError("unknown posture experience")
    record = json.loads(row[0])
    feedback = [json.loads(value[0]) for value in conn.execute(
        "SELECT record FROM posture_experience_feedback WHERE world_id=? AND experience_id=? ORDER BY position",
        (world_id, identity))]
    record["feedback"] = feedback
    record["history"] = []
    if history:
        for value, count in conn.execute(
            "SELECT record,feedback_count FROM posture_experience_revisions "
            "WHERE world_id=? AND experience_id=? ORDER BY revision", (world_id, identity)):
            previous = json.loads(value)
            previous["feedback"] = deepcopy(feedback[:count])
            record["history"].append(previous)
    return record, row[1]


class PostureJournal:
    def __init__(self, storage, world_id, *, legacy, guard, content_time, content_fields):
        self.storage, self.world_id = storage, world_id
        self.legacy, self.guard = legacy, guard
        self.content_time, self.content_fields = content_time, content_fields

    def _ready(self, conn):
        return conn.execute("SELECT 1 FROM posture_journals WHERE world_id=?", (self.world_id,)).fetchone() is not None

    def _get(self, conn, identity, *, history=True):
        return read_experience_record(conn, self.world_id, identity, history=history)

    def get(self, identity):
        def read(conn):
            if self._ready(conn):
                return self._get(conn, identity)[0]
            state = self.legacy()
            if identity not in state["experiences"]:
                raise ValueError("unknown posture experience")
            return deepcopy(state["experiences"][identity])
        self.guard()
        return self.storage._posture_journal_read(read)

    def get_visible(self, identity, *, subjects, context_id):
        """Authorize active subject/context access before loading the record."""
        def read(conn):
            if not subjects:
                return None
            if not self._ready(conn):
                record = self.legacy()["experiences"].get(identity)
                if (record is None or record["subject_id"] not in subjects
                        or not record["active"]
                        or (record["context_id"] != context_id and record["scope"] != "subject")):
                    return None
                return deepcopy(record)
            members = sorted(subjects)
            marks = ",".join("?" for _ in members)
            visible = conn.execute(
                "SELECT 1 FROM posture_experiences WHERE world_id=? AND experience_id=?"
                f" AND subject_id IN ({marks}) AND active=1"
                " AND (context_id=? OR scope='subject')",
                (self.world_id, identity, *members, context_id),
            ).fetchone()
            return self._get(conn, identity, history=False)[0] if visible else None
        self.guard()
        return self.storage._posture_journal_read(read)

    def list(self, *, subjects, context_id, include_inactive, history=True):
        def read(conn):
            if not self._ready(conn):
                values = self.legacy()["experiences"].values()
                records = [deepcopy(value) for value in values if value["subject_id"] in subjects
                           and (include_inactive or value["active"])
                           and (context_id is None or value["context_id"] == context_id or value["scope"] == "subject")]
                return [(record, self.content_time(record)) for record in records]

            # Read the visible current records and their child rows in batches.
            # Previously one SELECT per record (plus feedback/history SELECTs)
            # made even ordinary eight-item model retrieval grow in SQL round
            # trips as the lifelong journal accumulated entries.
            ordered_subjects = sorted(subjects)
            placeholders = ",".join("?" for _ in ordered_subjects)
            where = f" WHERE e.world_id=? AND e.subject_id IN ({placeholders})"
            args = [self.world_id, *ordered_subjects]
            if not include_inactive:
                where += " AND e.active=1"
            if context_id is not None:
                where += " AND (e.context_id=? OR e.scope='subject')"
                args.append(context_id)

            entries = {
                identity: [json.loads(raw), content_time]
                for identity, raw, content_time in conn.execute(
                    "SELECT e.experience_id, e.record, e.content_updated_at "
                    "FROM posture_experiences AS e" + where, args)
            }
            if not entries:
                return []
            for record, _ in entries.values():
                record["feedback"] = []
                record["history"] = []

            # Repeat the *same* World/subject/context predicates on the join.
            # Never fetch another Work's private feedback only to filter it
            # afterwards. Indexed child rows arrive in stable position order.
            join = (" JOIN posture_experiences AS e ON "
                    "e.world_id=j.world_id AND e.experience_id=j.experience_id")
            for identity, raw in conn.execute(
                "SELECT j.experience_id, j.record FROM posture_experience_feedback AS j"
                + join + where + " ORDER BY j.experience_id, j.position", args
            ):
                entries[identity][0]["feedback"].append(json.loads(raw))

            if history:
                for identity, raw, count in conn.execute(
                    "SELECT j.experience_id, j.record, j.feedback_count "
                    "FROM posture_experience_revisions AS j"
                    + join + where + " ORDER BY j.experience_id, j.revision", args
                ):
                    previous = json.loads(raw)
                    previous["feedback"] = deepcopy(entries[identity][0]["feedback"][:count])
                    entries[identity][0]["history"].append(previous)
            return [(record, content_time) for record, content_time in entries.values()]
        self.guard()
        return self.storage._posture_journal_read(read)

    def _insert(self, conn, record, *, content_time):
        identity = record["id"]
        plain = {key: value for key, value in record.items() if key not in {"history", "feedback"}}
        conn.execute("INSERT INTO posture_experiences VALUES (?,?,?,?,?,?,?,?,?)", (
            self.world_id, identity, record["subject_id"], record["context_id"], record["scope"],
            int(record["active"]), record["updated_at"], content_time, _json(plain)))
        feedback = record["feedback"]
        for position, value in enumerate(feedback):
            conn.execute("INSERT INTO posture_experience_feedback VALUES (?,?,?,?)",
                         (self.world_id, identity, position, _json(value)))
        history = record["history"]
        if len(history) != record["revision"] - 1:
            raise ValueError("posture revision history is incomplete")
        for revision, value in enumerate(history, 1):
            if value["revision"] != revision or value["id"] != identity:
                raise ValueError("posture revision history is inconsistent")
            prior_feedback = value["feedback"]
            if feedback[:len(prior_feedback)] != prior_feedback:
                raise ValueError("posture historical feedback is inconsistent")
            snapshot = {key: item for key, item in value.items() if key != "feedback"}
            conn.execute("INSERT INTO posture_experience_revisions VALUES (?,?,?,?,?)",
                         (self.world_id, identity, revision, _json(snapshot), len(prior_feedback)))

    def _import(self, conn):
        if self._ready(conn):
            return
        state = self.legacy()
        for record in state["experiences"].values():
            self._insert(conn, record, content_time=self.content_time(record))
        conn.execute("INSERT INTO posture_journals VALUES (?,1)", (self.world_id,))

    def add(self, record):
        def write(conn):
            self._import(conn)
            if conn.execute("SELECT 1 FROM posture_experiences WHERE world_id=? AND experience_id=?",
                            (self.world_id, record["id"])).fetchone():
                raise ValueError("posture identity already exists; revise it explicitly")
            self._insert(conn, record, content_time=record["updated_at"])
            return deepcopy(record)
        return self.storage._posture_journal_write(write, guard=self.guard)

    def revise(self, identity, expected_revision, update):
        def write(conn):
            self._import(conn)
            before, content_time = self._get(conn, identity, history=False)
            if type(expected_revision) is not int or before["revision"] != expected_revision:
                raise ValueError("posture revision conflict; read the latest experience")
            record = deepcopy(before)
            update(record)
            if record["revision"] != expected_revision + 1 or record["history"] != [
                    {key: value for key, value in before.items() if key != "history"}]:
                raise ValueError("posture revision must retain its previous snapshot")
            if record["feedback"][:len(before["feedback"])] != before["feedback"]:
                raise ValueError("posture feedback history cannot be rewritten")
            fields = (*self.content_fields(record), "scope", "active")
            if any(before.get(key) != record.get(key) for key in fields):
                content_time = record["updated_at"]
            plain = {key: value for key, value in record.items() if key not in {"history", "feedback"}}
            conn.execute("UPDATE posture_experiences SET scope=?,active=?,updated_at=?,content_updated_at=?,record=? "
                         "WHERE world_id=? AND experience_id=?", (record["scope"], int(record["active"]),
                         record["updated_at"], content_time, _json(plain), self.world_id, identity))
            snapshot = {key: value for key, value in before.items() if key not in {"history", "feedback"}}
            conn.execute("INSERT INTO posture_experience_revisions VALUES (?,?,?,?,?)",
                         (self.world_id, identity, expected_revision, _json(snapshot), len(before["feedback"])))
            for position in range(len(before["feedback"]), len(record["feedback"])):
                conn.execute("INSERT INTO posture_experience_feedback VALUES (?,?,?,?)",
                             (self.world_id, identity, position, _json(record["feedback"][position])))
            return self._get(conn, identity)[0]
        return self.storage._posture_journal_write(write, guard=self.guard)
