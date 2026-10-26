# Per Pulse

**A place to keep going.**

Per Pulse is an open-source workspace for connected, persistent conversations. Start in Home, open a focused session from a link inside the conversation, and bring a selected result back with its source attached.

Home and session pages are contexts of the same continuing subject. Each session has its own conversation history; a link opens that existing context without copying the entire Home conversation into it.

This first release provides an English interface, a local Python service, and a React client. It continues the existing Pulse Home/Work and Harness Kernel implementation.

## What you can do

- Create a session page from Home or another page, with a focused opening instruction.
- Insert lightweight page links directly into a message and reuse a page from several conversations.
- Continue in the linked page and return to the source while retaining your unsent draft and reading position.
- Bring selected text back to Home with a reference to its source page and message.
- Rename and search pages, follow breadcrumbs and backlinks, and reopen persisted conversations.
- Use a separate, clearly labeled example workspace to explore the interface without a model.
- Enable bounded local pulses: a context can continue from its retained history, and explicit computational connections can carry its results into related contexts.

The server stores pages, messages, and references in the existing Pulse persistence layer. Browser storage holds only local drafts and navigation positions, scoped to the persistent workspace and page. When no model is configured, the interface says **AI disconnected** and saves messages as queued input. It never substitutes a scripted answer for a model response.

## Run from source

Requirements: Python 3.12 or later and Node.js 20.19 or later. Run these commands from the repository root in a POSIX shell:

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

Open **http://127.0.0.1:8000**. On Windows, activate the environment with `.venv\Scripts\Activate.ps1` in PowerShell instead of `source`.

The ordinary workspace starts empty. Data persists in `~/.local/share/per-pulse` by default. Choose another directory or port with:

```sh
per-pulse --data-dir ./data/my-workspace --port 8000
```

### Explore the example

```sh
per-pulse --example
```

The example includes English notes and connected pages. The interface identifies these as authored sample material. Example mode always disables model requests, including when provider environment variables are set. Its data lives in an `example` child directory, separate from the ordinary workspace.

### Connect a model

Per Pulse uses the inherited HTTP Chat Completions adapter. Configure an endpoint that supports streaming OpenAI-compatible Chat Completions, tool calls, and usage reporting:

```sh
export P_PULSE_CHAT_ENDPOINT='https://your-provider.example/v1/chat/completions'
export P_PULSE_CHAT_MODEL='your-model-id'
export P_PULSE_API_KEY='your-api-key'
per-pulse
```

Replace the placeholders with your provider's values. The endpoint is a complete URL, not a base URL. Credentials stay in the server environment; the browser never receives them. You can use `P_PULSE_CREDENTIAL_ENV` to name a different environment variable containing the key.

If neither endpoint nor model is set, the workspace runs disconnected. Setting only one is a configuration error. Restart after changing provider configuration. Queued input from the ordinary workspace can be processed after connecting a model, so use a separate data directory when you want a fresh conversation.

Provider options inherited from Pulse include `P_PULSE_CHAT_TIMEOUT`, `P_PULSE_MAX_OUTPUT_TOKENS`, `P_PULSE_REASONING_FIELD=reasoning_content`, and `P_PULSE_PROVIDER_NAMESPACE=openrouter`. These describe adapter capabilities; compatibility with a specific remote model must be verified separately.

### Enable local pulse computation

```sh
per-pulse --pulse --pulse-budget 12
```

With a model configured, this enables local reentry and propagation through
explicit context connections, with at most twelve background pulse admissions
per rolling hour. Home and Work retain one subject and separate histories.
Refractory time, decaying inhibition and a causal depth ceiling bound the local
activity. The quota and causal evidence survive restart. Ordinary user messages
remain available when the background quota is exhausted.

`GET /api/pulse` exposes the current state and recent activation outcomes;
`POST /api/pulse/connections` connects source and target page IDs. Page links
remain navigation links. The detailed [pulse core contract](docs/pulse-core.md)
explains configuration, connection semantics, recovery and verification.

The default command leaves automatic pulses disabled. The quota counts
background admissions, not individual provider requests, tokens or money.

### Native workspace actions and sourced file results

Opt in with `per-pulse --allow-workspace-actions`. This enables native Kernel workspace proposals subject to action-specific approval, without creating a second runner. A Work's pending approval and exact-turn Stop remain in its original page; completed text-file changes can be opened from **Files from this page**, with original-action provenance and digest checks. The example workspace is always read-only. See [Core Harness](docs/harness-foundation.md) for authority, recovery, and limits. Persistent monetary reservations and native browser tools are not yet included.

## Develop

Run the backend in one terminal:

```sh
source .venv/bin/activate
per-pulse --example
```

Run the client in another:

```sh
npm --prefix web run dev
```

Open **http://localhost:5173**. Vite proxies `/api` to the backend on port 8000.

```sh
python -m pytest
npm --prefix web run typecheck
npm --prefix web run build
python -m build --no-isolation
```

Build the client before building a wheel. The wheel includes the compiled client, so an installed wheel serves the same interface without a Node.js process. No package has been published to PyPI by this repository's initial implementation.

## How the pieces fit

| Layer | Responsibility |
| --- | --- |
| `web/` | English conversation UI, inline references, page navigation, local drafts |
| `src/per_pulse/` | Local HTTP API, page projection, explicit example mode, CLI |
| `src/pulse_system/` | Existing World, native Home/Work, causal events, runtime service |
| `src/pulse_harness/` | Existing Kernel, model transport, turn history, tool and approval machinery |

Page IDs are native Work IDs, and message IDs are native causal-event IDs. Reference metadata lives in the same World's component state. Creating or following a reference does not copy another page's history or start an additional agent. Bringing text Home is an explicit user contribution with provenance; the source page remains open.

See [the API contract](docs/api.md), [pulse core](docs/pulse-core.md) and [source lineage](docs/source-lineage.md).

## Current scope

This is an alpha, single-user application bound to loopback. It does not yet provide account login, shared workspaces, cloud hosting, deletion/archive controls, or a model setup wizard. The page view shows the latest 500 messages; older events remain in the durable store. The first UI does not expose the inherited runtime's full operational or approval console.

The runtime is configured with workspace mutation disabled in this entry point. Live provider access is explicit. Automated integration tests use local test transports and validate persistence and routing; they do not certify real-model performance or autonomous work quality.

## License

Apache-2.0. The inherited Pulse copyright and attribution are preserved in [LICENSE](LICENSE) and [NOTICE](NOTICE).
