# Per Pulse

**A place to keep going.** Per Pulse is an open-source local workspace for one continuing computational subject, with Home, focused Work contexts, sourced return to Home, native Pulse activity, and explicit approval for workspace actions.

## Try the integrated alpha review candidate

The default `main` branch is **not yet the runnable application**. The active integrated product is maintained in [Draft PR #31](https://github.com/Lightmaze/Per-Pulse/pull/31), on `integration/installed-alpha-main-candidate-20261026`. It has **not** been merged, released, or deployed. Do not install from the default branch and expect the application to start.

To explore the authored, model-free example with the current reviewed source:

```sh
git clone --branch integration/installed-alpha-main-candidate-20261026 --single-branch https://github.com/Lightmaze/Per-Pulse.git
cd Per-Pulse
python -m venv .venv
source .venv/bin/activate
npm --prefix web ci
npm --prefix web run build
python -m pip install -e '.[dev]'
per-pulse --example
```

Requirements: Python 3.12+ and Node.js 20.19+. On Windows PowerShell, use `.venv\Scripts\Activate.ps1` in place of `source .venv/bin/activate`. Open **http://127.0.0.1:8000**. Example mode is isolated and does **not** call a model, even when provider credentials are set.

For real work, model setup, privacy boundaries, Pulse controls, and full development instructions, read the [candidate branch README](https://github.com/Lightmaze/Per-Pulse/blob/integration/installed-alpha-main-candidate-20261026/README.md) and [landing review gate](https://github.com/Lightmaze/Per-Pulse/blob/integration/installed-alpha-main-candidate-20261026/docs/alpha-landing-gate.md). External model learning benefits, continuous long-duration autonomy, and provider billing hard limits are **not** validated by the existing synthetic transport tests.

This main-branch navigation does not make PR #31 ready for merge. The original [P-Pulse project](https://github.com/Lightmaze/P-Pulse) is a separate upstream; upstream features are not automatically included in Per Pulse.

License: Apache-2.0 (the integrated candidate retains its original LICENSE and NOTICE).
