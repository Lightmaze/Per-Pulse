# Contributing

Read [AGENTS.md](AGENTS.md) before changing the runtime or interface. Continue
the existing World/Kernel and keep source attribution with returned results.

Follow the [source setup](README.md#run-from-source), then run:

```sh
python -m pytest
npm --prefix web run test
npm --prefix web run typecheck
npm --prefix web run build
python -m build --no-isolation
python scripts/verify_distribution.py
```

Keep each PR to one feature or fix. Explain the concrete user behavior, review
the diff, and exercise the affected browser flow. Record checks actually run
and distinguish test model fixtures from real provider calls. Add a regression
when it guards a meaningful failure.

Commit source and synthetic fixtures only. Do not attach private conversations,
workspace databases, images or credentials. Report vulnerabilities through the
process in [SECURITY.md](SECURITY.md).

See [the release workflow](docs/releasing.md) for packaging and cold installation.
