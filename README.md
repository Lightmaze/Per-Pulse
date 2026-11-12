# Per Pulse

**A place to keep going.**

Per Pulse is an open-source workspace for one continuing conversation. Start in
Home, open a focused page from a link inside the conversation, and bring a
selected result back with its source attached.

Home and pages are contexts of the same continuing subject. Each page keeps its
own history; a link opens that context without copying the Home conversation
into it. With a model connected, pages can also continue on their own within an
hourly budget.

The interface is available in English and 简体中文. Per Pulse is a local Python
service with a React client, built on the existing Pulse Home/Work and Harness
Kernel implementation.

## What you can do

- Create a page from Home or another page, link pages inline, and return to
  where you were with your unsent draft and reading position intact.
- Bring selected text Home with a link to its source message, or pass a
  selected result from one Work to another.
- Drop or paste up to five mixed PNG/JPEG, text or PDF files into a Home or
  Work draft; the saved attachments survive refresh and go to the model only
  when you press Send. You can also attach text materials (Markdown, CSV,
  JSON or code, up to 1 MiB), PDFs
  (up to 32 MiB, read as their text), or PNG/JPEG images (up to 32 MiB and
  50 megapixels). Preview them in Materials and send an exact source version.
  Reading an image sends it to your configured model, which must support image
  input; a large image is sent as a copy scaled to fit 2048 px and 1 MiB. Replies can include headings, tables and code blocks.
- Read public HTTPS text pages with their source URL and reading time.
  Inspect the exact text that was originally read even if the webpage later changes,
  then select a passage to keep as a sourced example for later Works; return to
  the original observation through its source Work.
- Let pages continue: background pulses within an hourly budget, or type
  `/tick 3` to give a page three more steps.
- Start autonomous practice from Activity with a weekly request allowance. Work and
  its sources persist through pauses, budget holds and restarts.
- Keep examples and lessons from your corrections. They are available across
  your pages unless you keep one to its page.
- Let the model change files in its workspace, and undo any change from the
  page that made it.
- Visit a shared Habitat region across Home and Works: approve a regional file
  change once, continue that file from another Work, and revisit it after restart.
  Later Works can discover and search past regions without knowing their names;
  version-checked writes avoid overwriting newer changes. Habitat writes still
  require Allow once and currently do not offer Undo.
- Browse and search the whole history, continue through large workspace
  directories, and reopen older file results from a long-running Work with
  their original source and version. Move a stopped workspace to another directory.

When no model is configured, the interface says **No model configured** and keeps
your messages as queued input. It never substitutes a scripted reply for a model.

## Install the local alpha

Requires Python 3.12 or later. Download
[`per_pulse-0.1.0a16-py3-none-any.whl`](https://github.com/Lightmaze/Per-Pulse/releases/tag/v0.1.0a16)
from the release, then install it in a virtual environment:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install ./per_pulse-0.1.0a16-py3-none-any.whl
per-pulse --example
```

Open **http://127.0.0.1:8000**. The wheel includes the compiled interface;
Node.js is needed only for source development. Stop with Ctrl+C, then run
`per-pulse` without `--example` for your own workspace.
See [installation](docs/install.md) for Windows commands, model setup and updates.

## Run from source

Requirements: Python 3.12 or later and Node.js 20.19 or later.

```sh
git clone https://github.com/Lightmaze/Per-Pulse.git
cd Per-Pulse
python -m venv .venv
source .venv/bin/activate
npm --prefix web ci
npm --prefix web run build
python -m pip install -e '.[dev]'
per-pulse
```

Open **http://127.0.0.1:8000**. On Windows, activate the environment with
`.venv\Scripts\Activate.ps1` in PowerShell.

Data lives in `~/.local/share/per-pulse` by default. Choose another directory
or port with `per-pulse --data-dir ./data/my-workspace --port 8000`.

## Connect a model

Per Pulse uses an OpenAI-compatible Chat Completions endpoint with streaming,
tool calls and usage reporting:

```sh
export P_PULSE_CHAT_ENDPOINT='https://your-provider.example/v1/chat/completions'
export P_PULSE_CHAT_MODEL='your-model-id'
export P_PULSE_API_KEY='your-api-key'
per-pulse doctor
per-pulse
```

The endpoint is a complete URL. The key stays in the server environment; the
browser never receives it. `per-pulse doctor` checks the local configuration
without contacting the provider. A model server on this computer or at a literal private LAN IP works too.
Private LAN HTTP must be explicitly enabled; it sends the model your task and
file context without transport encryption. See [Connect a model](docs/models.md)
for the required settings, PowerShell and other provider options.

## Let pages continue

With a model connected, Per Pulse lets a page continue from its own history in
the background, up to 12 background turns per hour. Change the budget with
`--pulse-budget N`, or turn it off with `--no-pulse`.

To give one page more time yourself, type in its composer:

- `/tick` — one more step;
- `/tick 3` — exactly three more steps (up to 256), one after another;
- `/tick stop` — stop before the next step.

A step continues the page's own conversation without a new instruction, and
starts only after the previous reply finishes. Writing your own message in that
page ends the run. Steps you ask for do not use the background budget. Pages can
also pass finished results to each other through connections; see
[Local pulses](docs/pulse-core.md).

Pages can learn connections from their activity, and their local rhythm adapts
across the workspace. Activity shows learned connections and lets you remove
them. Use `--no-adaptive-pulse` to freeze connection learning and keep a fixed rhythm.

## Start autonomous practice

With a model configured, open **Activity**, set a weekly request limit and select
**Start autonomous practice**. An original SELF departure can create native
Works and return source-linked results without you first sending a message.
**Pause autonomous practice** stops new autonomous sends; increasing the
allowance lets retained Work continue. A weekly slot counts a physical model
request, including retries and delegated requests, not a price or token cap.
See [Autonomous practice](docs/autonomous-practice.md) for details and limits.

## Explore the example

```sh
per-pulse --example
```

The example workspace contains English sample notes and connected pages. It
never calls a model, even when provider settings are present, and keeps its data
in a separate `example` directory.

## Other options

| Option | What it does |
| --- | --- |
| `--no-posture` | Don't keep examples and lessons from corrections (kept by default). See [Experience](docs/experience.md). |
| `--ask-before-edits` | Approve each file change before it applies (by default changes apply and can be undone). See [Workspace actions](docs/workspace-actions.md). |
| `--no-workspace-actions` | Don't let the model change files. |
| `per-pulse image save/inspect/restore` | Move a stopped workspace to a new directory. See [Workspace image](docs/image.md). |
| `python -m per_pulse.pulse_observer` | Record a running workspace's activity over time. See [Observer](docs/observer.md). |

## Develop

Run the backend and the client in two terminals:

```sh
per-pulse --example
npm --prefix web run dev
```

Open **http://localhost:5173**; Vite proxies `/api` to port 8000.

```sh
python -m pytest
npm --prefix web run test
npm --prefix web run typecheck
npm --prefix web run build
python -m build --no-isolation
```

Build the client before building a wheel; the wheel serves the compiled client.
Optional study tooling lives in [`research/`](research/README.md).

## How the pieces fit

| Layer | Responsibility |
| --- | --- |
| `web/` | Conversation UI, inline references, page navigation, local drafts |
| `src/per_pulse/` | Local HTTP API, page projection, example mode, CLI |
| `src/pulse_system/` | World, native Home/Work, causal events, pulses, runtime service |
| `src/pulse_harness/` | Kernel, model transport, turn history, tools and approvals |

Page IDs are native Work IDs and message IDs are native causal-event IDs.
References live in the same World store. Opening or linking a page never copies
another page's history or starts another agent.

## Current scope

This is a local, single-user alpha bound to loopback. It has no accounts,
shared workspaces, cloud hosting or deletion controls yet. Automated tests use
local test transports; they check behavior, not model quality.

All documentation is listed in [docs/README.md](docs/README.md).

## License

Apache-2.0. The inherited Pulse copyright and attribution are preserved in
[LICENSE](LICENSE) and [NOTICE](NOTICE).
