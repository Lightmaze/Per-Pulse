"""Guided setup and single-process launch for the complete P-Pulse package.

Configuration/credentials and runtime databases stay outside the tool workspace.
Setup does not call a provider; starting the host permits configured model activity.
"""
from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Iterator

from pulse_harness.adapters.chat_http import ChatHTTPConfig
from pulse_harness.core.errors import HarnessError


class SetupError(ValueError):
    pass


SETUP_SCHEMA_V1 = "p-pulse-setup.v1"
SETUP_SCHEMA_V2 = "p-pulse-setup.v2"


def default_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "p-pulse"


def _private_write(path: Path, content: str) -> None:
    fd, name = tempfile.mkstemp(prefix=".setup-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _private_read(path: Path) -> str:
    # Never print a configuration body or credential as part of a parse error.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise SetupError("configuration and credential files must be private regular files (chmod 600)")
            value = stream.read(65537)
            if len(value) > 65536:
                raise SetupError("configuration file is too large")
            return value.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise SetupError("cannot read private setup; run p-pulse setup in the selected home") from exc


def validate_config(value: object) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") not in {
        SETUP_SCHEMA_V1,
        SETUP_SCHEMA_V2,
    }:
        raise SetupError("unsupported setup schema")
    schema_version = value["schema_version"]
    allowed = {
        "schema_version", "endpoint", "model", "credential_source",
        "allow_local_http", "reasoning_field",
    }
    if schema_version == SETUP_SCHEMA_V2:
        allowed.add("provider_namespace")
    if set(value) != allowed:
        raise SetupError("invalid setup fields")
    provider_namespace = value.get("provider_namespace")
    if (value["credential_source"] not in {"environment", "file"}
            or type(value["allow_local_http"]) is not bool
            or not isinstance(value["endpoint"], str) or not isinstance(value["model"], str)
            or any(ord(c) < 32 for c in value["model"])
            or provider_namespace not in {None, "openrouter"}):
        raise SetupError("invalid setup values")
    try:
        ChatHTTPConfig(value["endpoint"], value["model"],
                       allow_local_http=value["allow_local_http"], reasoning_field=value["reasoning_field"],
                       provider_namespace=provider_namespace)
    except (ValueError, TypeError):
        raise SetupError("model endpoint, model name or reasoning field is invalid") from None
    return {**value, "provider_namespace": provider_namespace}


def configure(home: Path, endpoint: str, model: str, *, credential: str | None,
              allow_local_http: bool = False, reasoning_field: str | None = None,
              provider_namespace: str | None = None, replace: bool = False) -> dict:
    config = validate_config({"schema_version": SETUP_SCHEMA_V2, "endpoint": endpoint,
        "model": model, "credential_source": "file" if credential else "environment",
        "allow_local_http": allow_local_http, "reasoning_field": reasoning_field,
        "provider_namespace": provider_namespace})
    if credential is not None and (not credential or len(credential) > 8192 or any(c.isspace() for c in credential)):
        raise SetupError("credential must be a non-empty single-line value")
    home = home.expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (home / "model.json").exists() and not replace:
        raise SetupError("setup already exists; use setup --replace to change the model without deleting history")
    # Credentials use immutable, random filenames. Replacing model.json is the
    # final commit point; a failed setup never pairs an old endpoint with a new key.
    if credential:
        import uuid
        secret_name = f"credential-{uuid.uuid4().hex}"
        _private_write(home / secret_name, credential)
    else:
        secret_name = None
    persisted = {**config, "credential_file": secret_name}
    try:
        _private_write(home / "model.json", json.dumps(persisted, indent=2) + "\n")
    except Exception:
        if secret_name:
            (home / secret_name).unlink(missing_ok=True)
        raise
    return config


def load_config(home: Path) -> tuple[dict, str | None]:
    import re
    try:
        persisted = json.loads(_private_read(home / "model.json"))
    except (ValueError, TypeError) as exc:
        if isinstance(exc, SetupError):
            raise
        raise SetupError("invalid setup JSON; run setup --replace") from None
    if not isinstance(persisted, dict):
        raise SetupError("invalid setup")
    secret_name = persisted.pop("credential_file", None)
    config = validate_config(persisted)
    if config["credential_source"] == "file":
        if not isinstance(secret_name, str) or not re.fullmatch(r"credential-[0-9a-f]{32}", secret_name):
            raise SetupError("invalid credential reference")
        key = _private_read(home / secret_name)
    else:
        if secret_name is not None:
            raise SetupError("environment credentials cannot contain a file reference")
        key = os.environ.get("P_PULSE_API_KEY")
    if not key or len(key) > 8192 or any(c.isspace() for c in key):
        raise SetupError("model credential unavailable; set P_PULSE_API_KEY or run setup --replace")
    return config, key


@contextlib.contextmanager
def model_environment(config: dict, key: str) -> Iterator[None]:
    values = {"P_PULSE_CHAT_ENDPOINT": config["endpoint"], "P_PULSE_CHAT_MODEL": config["model"],
              "P_PULSE_CREDENTIAL_ENV": "P_PULSE_API_KEY", "P_PULSE_API_KEY": key,
              "P_PULSE_ALLOW_LOCAL_HTTP": "1" if config["allow_local_http"] else "0",
              "P_PULSE_REASONING_FIELD": config["reasoning_field"] or "",
              "P_PULSE_PROVIDER_NAMESPACE": config.get("provider_namespace") or ""}
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def reconcile_usage(home: Path, config: dict, key: str, *, limit: int = 25) -> dict:
    """Run one bounded provider-usage reconciliation pass, without booting Pulse."""

    if type(limit) is not int or not 1 <= limit <= 100:
        raise SetupError("usage reconciliation limit must be between 1 and 100")
    if config.get("provider_namespace") != "openrouter":
        raise SetupError(
            "usage reconciliation requires setup --provider-namespace openrouter"
        )
    database = home / "kernel.db"
    if not database.is_file():
        raise SetupError("Kernel database is unavailable; start P-Pulse before reconciling usage")

    from pulse_harness.adapters.openrouter_usage import OpenRouterUsageReconciler
    from pulse_harness.core import HarnessStore, ProviderUsageReconciliationManager

    store = HarnessStore(database)
    try:
        with model_environment(config, key), OpenRouterUsageReconciler() as reconciler:
            report = ProviderUsageReconciliationManager(
                store, reconciler
            ).reconcile_all(max_rounds=limit)
        return {
            "complete": report.remaining_unaccounted_rounds == 0,
            "reconciled_round_ids": list(report.reconciled_round_ids),
            "blockers": [
                {
                    "round_id": blocker.round_id,
                    "reason_code": blocker.reason_code,
                }
                for blocker in report.blockers
            ],
            "remaining_unaccounted_rounds": report.remaining_unaccounted_rounds,
        }
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p-pulse", description=__doc__)
    parser.add_argument("--home", type=Path, default=default_home(), help="private product data directory")
    subs = parser.add_subparsers(dest="command")
    setup = subs.add_parser("setup", help="choose a provider; no model call is made")
    setup.add_argument("--endpoint")
    setup.add_argument("--model")
    setup.add_argument("--credential-env", action="store_true", help="use P_PULSE_API_KEY; do not save its value")
    setup.add_argument("--allow-local-http", action="store_true", help="explicitly permit a loopback HTTP model")
    setup.add_argument("--reasoning-field", choices=["reasoning_content"])
    setup.add_argument("--provider-namespace", choices=["openrouter"],
                       help="record provider-specific accounting authority")
    setup.add_argument("--replace", action="store_true")
    run = subs.add_parser("start", help="start the runtime and included Web client")
    run.add_argument("--port", type=int, default=8000)
    run.add_argument("--command-image", help="enable approved commands using a preinstalled Docker image with python3; network disabled")
    coverme_choice = run.add_mutually_exclusive_group()
    coverme_choice.add_argument("--coverme", type=Path, metavar="WIKI_JSON",
                               help="enable CoverMe: review candidate replies against this Wiki snapshot")
    coverme_choice.add_argument("--no-coverme", action="store_true",
                               help="disable CoverMe for this start, including environment configuration")
    coverme = subs.add_parser("coverme", help="inspect a CoverMe Wiki without calling a model")
    coverme_commands = coverme.add_subparsers(dest="coverme_command", required=True)
    validate_wiki = coverme_commands.add_parser("validate", help="validate the complete Wiki snapshot and its index")
    validate_wiki.add_argument("wiki", type=Path)
    from .expert import add_commands
    add_commands(subs, run)
    from . import image
    image.add_commands(subs)
    subs.add_parser("status", help="show local setup status without contacting the model")
    reconcile = subs.add_parser(
        "reconcile-usage", help="perform one explicit provider token-usage lookup pass"
    )
    reconcile.add_argument(
        "--limit", type=int, default=25,
        help="maximum gaps inspected in this pass (1-100; default: 25)",
    )
    args = parser.parse_args(argv)
    home = args.home.expanduser().resolve()
    try:
        if args.command == "image":
            try:
                return image.execute(args, home)
            except image.ImageError as error:
                raise SetupError(str(error)) from error
        if (home / image.INCOMPLETE).exists():
            raise SetupError("this home has an incomplete image restore; do not start or overwrite it")
        if args.command == "expert":
            from .expert import execute
            try:
                return execute(args, home)
            except (ValueError, UnicodeError):
                raise SetupError("Expert computation input is invalid; see docs/expert-computation.md") from None
        if args.command == "coverme":
            from pulse_harness.plugins.coverme import WikiIndex
            try:
                index = WikiIndex.load(args.wiki.expanduser().resolve())
            except (OSError, UnicodeError, ValueError):
                raise SetupError("CoverMe Wiki is invalid or unreadable; see docs/coverme.md") from None
            print(json.dumps({"valid": True, "entries": len(index.entries),
                              "index_sha256": index.fingerprint, "model_calls": 0}))
            return 0
        if args.command == "setup":
            endpoint = args.endpoint or input("Streaming Chat Completions endpoint (HTTPS): ").strip()
            model = args.model or input("Model ID: ").strip()
            if not args.credential_env:
                print("The key will be saved in a private local file outside the Agent workspace.")
            key = None if args.credential_env else getpass.getpass("API key (hidden): ")
            configure(home, endpoint, model, credential=key, allow_local_http=args.allow_local_http,
                      reasoning_field=args.reasoning_field,
                      provider_namespace=args.provider_namespace, replace=args.replace)
            print("Setup saved. No model request was sent. Next: p-pulse --home " + str(home) + " start")
            return 0
        config, key = load_config(home)
        if args.command == "status":
            print(json.dumps({"model": config["model"], "credential_available": bool(key),
                              "home": str(home), "provider_namespace": config["provider_namespace"],
                              "provider_verified": False}))
            return 0
        if args.command == "reconcile-usage":
            report = reconcile_usage(home, config, key, limit=args.limit)
            print(json.dumps(report, sort_keys=True))
            return 0 if report["complete"] else 3
        if args.command not in {None, "start"}:
            raise SetupError("unsupported command")
        from pulse_system.service import serve
        if serve._web_dir() is None:
            raise SetupError("Web bundle is missing; install the complete product wheel or build with scripts/build_product.py")
        workspace = home / "workspace"
        workspace.mkdir(exist_ok=True, mode=0o700)
        port = getattr(args, "port", 8000)
        if not 1 <= port <= 65535:
            raise SetupError("port must be between 1 and 65535")
        print("Starting permits model calls. Persistent spend limits are not yet supplied by this launcher.")
        selected_image = getattr(args, "command_image", None)
        previous_image = os.environ.get("P_PULSE_COMMAND_IMAGE")
        previous_coverme = {name: os.environ.get(name) for name in ("P_PULSE_COVERME", "P_PULSE_COVERME_WIKI", "P_PULSE_EXPERT_COMPUTATION")}
        if getattr(args, "expert_computation", False):
            os.environ["P_PULSE_EXPERT_COMPUTATION"] = "1"
        elif getattr(args, "no_expert_computation", False):
            os.environ["P_PULSE_EXPERT_COMPUTATION"] = "0"
        selected_wiki = getattr(args, "coverme", None)
        if selected_wiki is not None:
            os.environ["P_PULSE_COVERME"] = "1"
            os.environ["P_PULSE_COVERME_WIKI"] = str(selected_wiki.expanduser().resolve())
        elif getattr(args, "no_coverme", False):
            os.environ["P_PULSE_COVERME"] = "0"
        if selected_image:
            os.environ["P_PULSE_COMMAND_IMAGE"] = selected_image
        try:
            from pulse_system.service.coverme import load_selected_wiki
            try:
                load_selected_wiki(workspace)
            except ValueError:
                raise SetupError("CoverMe requires a valid Wiki JSON outside the writable workspace; use coverme validate") from None
            from pulse_system.service.expert_computation import expert_enabled
            try:
                expert_enabled()
            except ValueError:
                raise SetupError("P_PULSE_EXPERT_COMPUTATION must be 0 or 1") from None
            with model_environment(config, key), contextlib.chdir(home):
                return serve.main(["--profile", "workspace", "--port", str(port),
                    "--origin", f"http://127.0.0.1:{port}", "--origin", f"http://localhost:{port}",
                    "--db", str(home / "world.db"), "--harness-db", str(home / "kernel.db"),
                    "--workspace", str(workspace), "--harness-checkpoints", str(home / "checkpoints")])
        finally:
            for name, value in previous_coverme.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            if previous_image is None:
                os.environ.pop("P_PULSE_COMMAND_IMAGE", None)
            else:
                os.environ["P_PULSE_COMMAND_IMAGE"] = previous_image
    except SetupError as exc:
        print("P-Pulse: " + str(exc), file=sys.stderr)
        return 2
    except HarnessError as exc:
        print("P-Pulse usage reconciliation failed: " + exc.code, file=sys.stderr)
        return 2
    except (OSError, EOFError):
        # Do not echo arbitrary file/JSON/credential bodies on error.
        print("P-Pulse setup/start failed. Check private file permissions and run p-pulse --home "
              + str(home) + " setup (use --replace for an existing setup).", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
