"""Operator CLI for the same native expert computation sidecar."""
import json
from pathlib import Path

from pulse_harness.plugins.expert_computation import ExpertStore, REASONS


def add_commands(subs, run):
    choice = run.add_mutually_exclusive_group()
    choice.add_argument("--expert-computation", action="store_true", help="compute expert contributions with the configured model (at most 2 extra calls per turn)")
    choice.add_argument("--no-expert-computation", action="store_true", help="disable expert computation for this start")
    parser = subs.add_parser("expert", help="manage expert experience and explicit outcome feedback; no model calls")
    commands = parser.add_subparsers(dest="expert_command", required=True)
    commands.add_parser("status")
    imp = commands.add_parser("import", help="import a JSON array of attributed episodes")
    imp.add_argument("file", type=Path)
    imp.add_argument("--owner", default="shared", help="shared operator corpus, or exact world:subject owner")
    listing = commands.add_parser("decisions")
    listing.add_argument("--limit", type=int, default=20)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("decision")
    feedback = commands.add_parser("feedback")
    feedback.add_argument("decision")
    feedback.add_argument("--score", type=float, required=True)
    feedback.add_argument("--reason", choices=sorted(REASONS), required=True)
    feedback.add_argument("--evidence", required=True)
    feedback.add_argument("--preferred-episode")
    capture = commands.add_parser("capture", help="retain a completed native experience with operator guidance")
    capture.add_argument("decision")
    capture.add_argument("--id", required=True)
    capture.add_argument("--guidance", required=True)
    capture.add_argument("--project", required=True)


def execute(args, home):
    with_store = home / "kernel.db.expert.sqlite3"
    store = ExpertStore(with_store)
    try:
        command = args.expert_command
        if command == "status":
            result = store.status()
        elif command == "import":
            with args.file.open("rb") as stream:
                data = stream.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise ValueError("episode import exceeds 4 MiB")
            result = {"imported": store.import_episodes(json.loads(data), owner=args.owner)}
        elif command == "decisions":
            result = store.decisions(limit=args.limit)
        elif command == "inspect":
            result = store.decision(args.decision)
        elif command == "feedback":
            result = store.feedback(args.decision, score=args.score, reason=args.reason,
                                    evidence=args.evidence, preferred_episode=args.preferred_episode)
        elif command == "capture":
            result = store.capture(args.decision, episode_id=args.id, guidance=args.guidance, project=args.project)
        else:
            raise ValueError("unknown expert command")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        store.close()
