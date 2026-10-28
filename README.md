# Per Pulse

**A place to keep going.**

Per Pulse is an open-source workspace for connected, persistent conversations. Start in Home, open a focused session from a link inside the conversation, and bring a selected result back with its source attached.

Home and session pages are contexts of the same continuing subject. Each session has its own conversation history; a link opens that existing context without copying the entire Home conversation into it.

The alpha candidate provides English and 简体中文 interface controls, a separate reply-language preference, a local Python service, and a React client. It continues the existing Pulse Home/Work and Harness Kernel implementation.

## What you can do

- Create a session page from Home or another page, with a focused opening instruction.
- Insert lightweight page links directly into a message and reuse a page from several conversations.
- Continue in the linked page and return to the source while retaining your unsent draft and reading position.
- Bring selected text back to Home with a reference to its source page and message.
- Rename and search pages, follow breadcrumbs and backlinks, and reopen persisted conversations.
- Browse earlier messages in bounded ranges, then open an original message to continue, link, or bring a selected passage Home.
- Open **Materials** beside the message composer to upload and preview a text file, reuse an existing file, and attach the version you inspected to your draft.
- Read structured replies and notes with headings, lists, tables, and code blocks. Copy a code block or the original Markdown, and keep native page links connected to their source.
- Recognize a native selected-history replay while it runs, inspect its original messages, and retain the interpretation's provenance when saving an example or bringing it Home.
- Use a separate, clearly labeled example workspace to explore the interface without a model.
- Enable bounded local pulses: a context can continue from its retained history, and explicit computational connections can carry its results into related contexts.

The server stores pages, messages, and references in the existing Pulse persistence layer. Browser storage holds only local drafts and navigation positions, scoped to the persistent workspace and page. When no model is configured, the interface says **AI disconnected** and saves messages as queued input. It never substitutes a scripted answer for a model response.

Message formatting uses CommonMark with GitHub-style tables and task lists.
Raw HTML remains text, and image references open only when selected; reading a
message does not automatically fetch a remote image. Code stays literal, even
when it contains the name of a linked page. A reference that appears only in
code or across separate formatting spans keeps a separate page link below the
message. **Copy** preserves the original message text, including Markdown;
**Copy code** preserves the displayed code's spacing and line breaks.

The existing `replay` tool lets the model reconsider one to four saved messages
within its current turn. After its successful native entry, only the selected
history reader remains available for that turn; finishing restores ordinary
tools for the next turn. The interface shows this activity and provides verified
links to the original input or reply. The raw message keeps its full provenance
prefix when copied. Saved examples and lessons retain the replay origin even
when the selected passage omits that prefix; shared experience exposes its
tentative nature without copying the private source material. Bringing a
selected interpretation Home also preserves its nature in the actual input.
This bounded regime retains the original conversation. It is not memory
isolation, sleep scheduling, new external evidence, or proof of model learning.

## Run from source

Requirements: Python 3.12 or later and Node.js 20.19 or later. **The default `main` branch now links to this Draft PR #31, but still does not contain the runnable application.** To try the working review version, explicitly check out that branch (not an empty default checkout):

```sh
git clone --branch integration/installed-alpha-main-candidate-20261026 --single-branch https://github.com/Lightmaze/Per-Pulse.git
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

Provider options inherited from Pulse include `P_PULSE_CHAT_TIMEOUT`, `P_PULSE_MAX_OUTPUT_TOKENS`, `P_PULSE_REASONING_FIELD=reasoning_content`, and `P_PULSE_PROVIDER_NAMESPACE=openrouter`. Per Pulse also enforces a default **8 MiB maximum serialized request body** at the outbound Chat Completions boundary, configurable with `P_PULSE_MAX_REQUEST_BYTES`. This rejects oversized requests *before network send*, including those arising from long history, side computations, tool schemas or image parts. It never silently truncates the saved context; a blocked turn remains visible for review. This byte guard is **not** a context-token limit, provider bill cap, or solution to automatic long-history management. Provider compatibility must be verified separately.

### Bring source material into a conversation

Open **Materials**, choose a UTF-8 text file up to 1 MiB, and select **Upload
file**. Inspect the preview, then **Add to draft**. Your existing text and page
links stay in place. Tell Per Pulse what you want to do with the material and
send the message when it is ready. Existing files can be reopened from the
same dialog without uploading them again.

Files belong to the shared native workspace and can be read from any page;
conversation histories remain separate. Each Work keeps its own attachment
record without copying the shared file. A source chip identifies the original
name and version; reopening or sending it checks that the bytes still match.
Remove the chip to leave the file out of a message. Uploading or previewing does
not run the model, and adding a reference does not mean the model has read the file. With a configured
provider, the existing read tools can open it when working on your request.
Uploads create new files and do not grant model write permissions.

Markdown materials and file results have **Read** and **Source** views. Headings,
lists, tables and code are readable while the original text remains available.
Images appear as descriptions; links to external sources open only when chosen.
CSV and TSV files also have a table view. Values remain text, including leading
zeros and empty cells. Use **First row contains headings** to match the file.
The view shows up to 200 data rows and 64 columns, with keyboard scrolling for
wide tables. **Columns** lets you compare a subset without changing the file;
your choices survive switching between Read and Source during that preview.
A partial preview is labelled and omits an unfinished trailing
row; irregular quotes, unequal rows or wider files fall back to source text.
The **Add to draft** action stays visible while reading a long material.

### Keep concrete examples and learn from corrections

Enable attributed experience when starting the same workspace:

```sh
per-pulse --posture
```

Use **Save example** on a completed reply to retain an exact passage, an optional
note, and optionally a later message from that page. Selecting text first fills
the passage editor when that selection matches the original text. An example
can stand on its own without a rule or evaluation. You can save one from a
previous reply even while the model is disconnected. The source passage stays
intact when you later edit its note.

With a model connected, explain what worked or what should change after a reply.
The model may save a conditional lesson with its original result and your
correction. **Settings → View this page’s experience** lets you inspect that
source, refine the guidance and its exceptions, pause it, or explicitly make
it available in other pages. The model can also retain a concrete example.
Model capture is optional: a correction does not guarantee that anything is saved.

In a later Work, **Give feedback** records your observation of a shared lesson or example.
You can optionally link a completed reply from that Work; the source page's
feedback history then opens the exact result, including older messages outside
the recent window. A described observation can also stand without a linked
reply. **No clear benefit** records a neutral observation and keeps the experience's
current active state. Harmful feedback pauses it; only its source page can
edit or reactivate it. A lesson's abstraction or an example's selected passage
and note can travel, while the surrounding conversation and private feedback
stay at the source.

Linking a result establishes where to inspect it, not that the experience caused
it. Saving an example does not certify its success or future applicability.
Real-provider learning benefits still need output comparison and review.
Example mode remains model-free and does not enable experience learning.

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

An optional `--pulse-hourly-tokens N` adds a separate background token
*admission* envelope. Each local activation reserves
`--pulse-token-reservation` tokens (default 8192) until accounted usage is
available. A positive `N` must cover at least one reservation; the CLI rejects
an impossible pairing rather than silently starving every local activation.
Use `N=0` to intentionally prevent new background token admission. This is
not a hard model-token or monetary spending limit.

The default command leaves automatic pulses disabled. The quota counts
background admissions, not individual provider requests, tokens or money.

### Observe a continuing body over actual elapsed time

To record an already-running local Per Pulse instance over one hour without
creating new tasks, model calls, or another scheduler, use a **separate terminal**:

```sh
python -m per_pulse.pulse_observer --url http://127.0.0.1:8000 \
  --duration-seconds 3600 --interval-seconds 10 \
  --output ./private-pulse-observation.jsonl
```

This read-only observer polls native `/api/pulse/observation` (one response
binds the persistent World, current service instance and Pulse status),
retains one World identity, records queue/budget snapshots and changed local
activation IDs with source links, and writes a private, create-only JSONL file
(mode 0600 on POSIX). Interrupted runs have no completed success footer unless
the observer catches the interruption; a caught interruption is marked
`interrupted`. Poll failures and a full twenty-event server window are
explicit evidence gaps, never silently counted as successful endurance.
The observed event count is a **lower bound**, not an exhaustive ledger read.
The native bootstrap exposes an ephemeral `serviceInstanceId`: when a running
service restarts under the same persistent World identity, the observer ends
with `service_restarted` instead of claiming uninterrupted process activity.
The Context Body and its history can remain persistent across that restart.

This command does not enable Pulse or a provider for you: start Per Pulse
normally, with `--pulse` and any explicitly authorized model, before observing.
A connected-provider status is not proof that a real provider ran, that
background activity improved a result, or that currency was capped. For a
long-horizon claim, inspect the actual file, elapsed wall time, native sources,
provider request/usage evidence, and whether relevant Works yielded useful
new outcomes; do not use a short synthetic CI run as that evidence.

### Native workspace actions and sourced file results

Opt in with `per-pulse --allow-workspace-actions`. This enables native Kernel workspace proposals subject to action-specific approval, without creating a second runner. A Work's pending approval and exact-turn Stop remain in its original page; completed text-file changes can be opened from **Files from this page**, with original-action provenance and digest checks. Example mode keeps model requests and model file changes disabled. See [Core Harness](docs/harness-foundation.md) for authority, recovery, and limits. Persistent monetary reservations and native browser tools are not yet included.

**Download file** takes the complete verified text result out of the workspace.
It checks the file again when requested and verifies the received bytes before
starting the browser download. A later overwrite, protected content, or missing
file produces an explanation; **Refresh file preview** checks its current state.
Downloads preserve UTF-8 text, line endings and the original filename, including
non-ASCII names. Redacted previews cannot be downloaded as original files.
This works for existing results while disconnected and after restart. File
access remains limited to text up to 1 MiB; the native write path currently
has a separate 64 KiB output bound. It does not restore older overwritten bytes.

The **Materials** flow accepts UTF-8 text, Markdown, CSV, JSON or code in
Home or Work. **Upload file** saves the original bytes; **Add to draft** selects
the inspected version; only **Send** submits it to the conversation.
Text and file-only drafts survive browser refresh. Each
message can include up to five files of at most 1 MiB each. PDF, images and
other binary files are not supported. A source chip opens its original text
preview; **Bring to Home** lets you include the source files with your selected
message while preserving their original page links.

The native input carries paths and recorded digests, not a claim that a model
read the files. `reading.open` can check that expected version before returning
its first passage, and continuation verifies the same source. Changed or
missing selected files are refused before sending; replacement bytes are not
silently substituted. Upload receipts and files persist across restart and
the existing stopped Pulse Image transfer. The native source API lists the latest
100 receipts per page, with a workspace limit of 1,024 receipts; older message
attachments remain accessible. Source-chip previews are bounded to 64 KiB and
redact sensitive values. The workspace library previews up to 64,000 characters.
Files are shared native workspace material, not isolated
per-page filesystem permissions.

A lost upload response is shown as unconfirmed and never retried automatically.
Open **Materials → uploads/** to inspect the saved file and add it to the draft.
The native file creation
and receipt storage are separate durable effects: a process or storage failure
between them can leave an unlisted file in `workspace/uploads`. Inspect that
directory before manually uploading again when no receipt can be recovered.

### Carry a stopped workspace to another directory (POSIX alpha)

Per Pulse uses the **existing native cold Pulse Image** format, not a second
history store or a live agent clone. After cleanly stopping the server, save
a private archive outside the source directory:

```sh
per-pulse image save ./per-pulse-private.zip --data-dir ~/.local/share/per-pulse
per-pulse image inspect ./per-pulse-private.zip
per-pulse image restore ./per-pulse-private.zip --data-dir ./restored-per-pulse
per-pulse --data-dir ./restored-per-pulse
```

Restore requires a **new, nonexistent directory** and an archive made with
a compatible **Per Pulse** implementation; an upstream P-Pulse image alone
is not assumed to be interchangeable. Save refuses an active World or
unfinished Kernel turns/actions. Restore checks member digests and the
matching Python implementation before writing, preserves the native
World/Kernel identities and source links, and does not start any model.
If a restore fails, do not start its incomplete destination. The archive
contains **private conversations, histories and workspace files**: do not
publish or share it. Model credentials and external tool/resource bindings
must be configured deliberately after restore.

This is a **cold copy**: it does not transfer a running process, merge later
changes made to the original home, or copy browser-local unsent drafts and
scroll positions. The old and restored homes can diverge; treat the
restored copy as the continuation you choose to use, not a synchronized
second subject. Image save/restore currently support POSIX hosts only.
Compatibility is bound to current Python source code, not a guarantee of
semantic equivalence across other model/provider versions.

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

This is an alpha, single-user application bound to loopback. It does not yet provide account login, shared workspaces, cloud hosting, deletion/archive controls, or a model setup wizard. The main conversation shows the latest 500 messages. **Earlier messages** reads older native history in groups of 100; **Find a message** searches the whole current conversation. Opening a result retains its original source and the current draft. The first UI does not expose the inherited runtime's full operational or approval console.

Workspace mutation remains disabled by default and requires an explicit `--allow-workspace-actions` opt-in plus approval of each actionable proposal. Live provider access is explicit. Automated integration tests use local test transports and validate persistence and routing; they do not certify real-model performance or autonomous work quality.

## License

Apache-2.0. The inherited Pulse copyright and attribution are preserved in [LICENSE](LICENSE) and [NOTICE](NOTICE).
