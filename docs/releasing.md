# Releasing Per Pulse

## Prepare a candidate

1. Choose the package version in `pyproject.toml`, set the same `__version__` in
   `src/per_pulse/__init__.py`, and write `docs/releases/v<VERSION>.md`. Update
   the download filename in the installation guide and README. Keep changes
   in a reviewed PR.
2. Build the client, run the normal checks, and build the distributions:

   ```sh
   npm --prefix web ci
   npm --prefix web run build
   python -m pip install -e '.[dev]'
   python -m pytest
   npm --prefix web run test
   npm --prefix web run typecheck
   python -m build --no-isolation
   python scripts/verify_distribution.py
   ```

3. The package workflow checks the actual wheel and source archive for the
   compiled client, dependency notices, license, required documentation and
   unwanted local/private file paths. It rebuilds the source archive without
   Node and compares every wheel member with the original. It installs the
   original wheel, with normal dependency resolution, into fresh environments
   on Linux, Windows and macOS. Native first use/restart runs on all three;
   installed Chromium first use and PNG/JPEG material reading run on Linux.
   The Linux job then installs pytest as a test-fixture dependency and checks
   webpage source observations and history recall in the installed application.
4. Review the normal CI and package workflow results on the exact candidate.
   Merge the candidate, then wait for full `Verify Per Pulse` CI on the exact
   main commit. Read review comments and inspect the actual browser flow.

Use a clean `dist/` when building a new version; the verifier requires exactly
one wheel and one source archive. Never put personal workspaces or images in a
release. Automated tests use isolated sample data and explicit model fixtures;
real provider smoke runs require an operator's configured provider.

## Publish

Merging a new package version is the release decision. After `Verify Per Pulse`
finishes successfully on main, `Package Per Pulse` checks out that exact SHA.
If the version already has a release, it stops without rebuilding or changing
the published assets. Main runs are serialized to prevent competing releases.

For a new version, the workflow rebuilds and verifies the archives and completes
the cold-install matrix. Publication rechecks successful main `ci.yml` against
the exact candidate SHA. It then creates the matching `v<VERSION>` tag and
publishes a GitHub prerelease containing the wheel, source archive and
`SHA256SUMS`, using the version's release notes. An existing tag must identify
that exact commit. Failed main CI never starts publication; failed packaging
or installation never reaches the publish job.

Rerun the failed package workflow after correcting an environment failure;
code corrections need their own reviewed commit and successful CI. An existing
release is never silently overwritten. Inspect the published assets and
checksums before announcing the download.
