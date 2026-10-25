# Per Pulse

**A place to keep going.**

Per Pulse is an open-source workspace for connected, persistent conversations. Start in Home, open a focused session from a link inside the conversation, and bring a selected result back with its source attached.

Home and session pages are contexts of the same continuing subject. Each session has its own conversation history; a link opens that existing context without copying the entire Home conversation into it.

This implementation provides English and Simplified Chinese interfaces, a local Python service, and a React client. It continues the existing Pulse Home/Work and Harness Kernel implementation.

## What you can do

- Create a session page from Home or another page, with a focused opening instruction.
- Insert lightweight page links directly into a message and reuse a page from several conversations.
- Continue in the linked page and return to the source while retaining your unsent draft and reading position.
- Bring selected text back to Home with a reference to its source page and message.
- Rename and search pages, follow breadcrumbs and backlinks, and reopen persisted conversations.
- Use a separate, clearly labeled example workspace to explore the interface without a model.
- Choose English or Simplified Chinese interface text, and set an independent response-language preference.

The server stores pages, messages, references, and the response-language preference in the existing Pulse persistence layer. Browser storage holds local drafts and navigation positions, scoped to the persistent workspace and page, plus the interface-language preference for this browser. When no model is configured, the interface says **AI disconnected** and saves messages as queued input. It never substitutes a scripted answer for a model response.

### Languages

Open **Settings → Language** to choose **System language**, **English**, or **简体中文** for the interface. The choice takes effect without reloading the workspace and is remembered in the current browser. System follows the first supported language in the browser's preferences, using Simplified Chinese for Chinese and falling back to English when neither language is present.

**Reply language** is separate: **Follow conversation**, **English**, or **简体中文**. It is saved with the current workspace, survives a server restart, and applies to the next new reply. A reply already in progress keeps its chosen language through tool use and approval continuation. Follow conversation leaves language to the task and conversation; an explicit language instruction in a task takes priority over a saved preference. Changing interface language does not change this preference.

Language settings preserve existing messages, page names, reference labels, drafts, and source material. The authored example notes remain in English. See [language support and maintenance](docs/language-support.md) for the contract and integration checklist.

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
| `web/` | English/Chinese conversation UI, interface-language preference, inline references, page navigation, local drafts |
| `src/per_pulse/` | Local HTTP API, page projection, explicit example mode, CLI |
| `src/pulse_system/` | Existing World, native Home/Work, causal events, runtime service |
| `src/pulse_harness/` | Existing Kernel, model transport, turn history, tool and approval machinery |

Page IDs are native Work IDs, and message IDs are native causal-event IDs. Reference metadata lives in the same World's component state. Creating or following a reference does not copy another page's history or start an additional agent. Bringing text Home is an explicit user contribution with provenance; the source page remains open.

See [the API contract](docs/api.md) and [source lineage](docs/source-lineage.md).

## Current scope

This is an alpha, single-user application bound to loopback. It does not yet provide account login, shared workspaces, cloud hosting, deletion/archive controls, or a model setup wizard. The page view shows the latest 500 messages; older events remain in the durable store. The first UI does not expose the inherited runtime's full operational or approval console.

The runtime is configured with workspace mutation disabled in this entry point. Live provider access is explicit. Automated integration tests use local test transports and validate persistence and routing; they do not certify real-model performance or autonomous work quality.

## License

Apache-2.0. The inherited Pulse copyright and attribution are preserved in [LICENSE](LICENSE) and [NOTICE](NOTICE).
