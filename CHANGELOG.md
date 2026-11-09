# Changelog

## Unreleased

- When a text-only model refuses a request that carries an image, send it once more without the image and tell the model why, instead of failing the turn with an unrelated hint about conversation length. Later images in the session are left out with the same note.

## 0.1.0a10

- Open a previously cited experience directly from the persistent World journal without rescanning the full history, preserving original and shared-Work visibility rules.
- Keep native weekly request provenance valid through deep Home/Work and tool chains instead of refusing at a fixed 256-event boundary; continue rejecting missing, cyclic and cross-World roots.
- Put ongoing work, pause/resume and weekly allowance first in Activity. Expand hourly token details and page connections only when needed, with English/Chinese navigation.
- Paste PNG/JPEG screenshots directly into an unsent Home or Work draft. Keep the exact source-backed image bytes and per-page draft across reload and navigation; send only on explicit user action.

## 0.1.0a9

- Connect to an operator-selected OpenAI-compatible model server at a literal private LAN IPv4 or ULA IPv6 address, using the existing World, Kernel and model transport.
- Add bilingual Settings commands for POSIX and PowerShell. Require explicit private HTTP and separate no-auth opt-ins; warn that unencrypted LAN model calls transmit task and file context.
- Keep hosted and local provider modes unchanged. Verify address restrictions, synthetic streaming transport and the installed disconnected browser setup path; a real LAN model remains a user-configured service.

## 0.1.0a8

- Browse beyond the first 200 files in a large Materials directory with bounded continuation, change detection and a visible refresh path, while keeping the original file access boundary.
- Load growing experience journals in batches rather than executing a SQL query for every experience and its feedback/history; retain attribution and Work privacy.
- Keep exact source passages containing literal omission markers in examples, readings and command-output memories; genuine incomplete sources remain incomplete.
- On temporary Home observation failures, preserve previously verified Work names as read-only context and offer a refresh. Recheck source links before enabling them again.

## 0.1.0a7

- Keep experience growing beyond the aggregate 128-record / 4 MiB lifetime
  quota, preserving original sources, old revisions, feedback and stop controls
  in the existing World database. Keep offline research readers compatible.
- Continue older experience lists and oversized experience details through
  bounded source-checked pages. Read the rest of a long public webpage through
  explicit ranges that verify the observed text and resolved source.
- Accept nullable optional provider fields, standard SSE BOM/CR/CRLF framing,
  and streamed refusal text while retaining protocol validation and restart.
- Keep complete literal file-action arguments reviewable through the original
  model-call authority, and download verified outputs as their original bytes.
- Preserve native file, Notice and lesson text containing literal omission
  markers. Correct Windows UTF-16 path lengths for non-BMP filenames.
- Bound diagnostic backlog and retry failed writes without interrupting native
  work. Keep draft save failures visible, editable, copyable and recoverable.
- Present a new page's source author separately from the user's request across
  conversation, history and search. Label local configuration refresh accurately.

- Preserve ordinary JSON replies as text through later messages, context
  fitting, optional review and workspace restart. Decode internal tool
  envelopes using the saved model round's authority.

- Download verified local file results containing literal omission markers,
  without mistaking their text for permission or provenance metadata. Source
  previews report actual local clipping rather than inferred redaction.

## 0.1.0a6

- Keep long-lived Works reachable: browse native histories beyond 32 entries,
  search persisted Work titles by native tool, and find older Works in a bounded
  sidebar with a visible current Work and bilingual search.
- Recover autonomous weekly request tickets across long, multi-week restarts,
  retaining the same World ownership and accounting rules.
- Improve provider tool-call compatibility and persistence for OpenAI/Azure,
  Anthropic, DeepSeek, Gemini and OpenRouter streams. Preserve reasoning and
  tool metadata needed for continuation without fabricating tool responses.
- Extend native and browser tests for long Work histories and provider tool
  continuation. Existing v0.1.0a5 artifacts and tags remain unchanged.


## 0.1.0a5

- Include the two fixed public synthetic TLS test fixtures in the source archive, so
  extracted sources can reproduce native HTTPS/TLS tests. Keep private-key and
  certificate exclusions intact for all other paths and for the installed wheel.
- Require those exact fixture hashes in source archive verification; a missing
  or changed fixture now fails packaging. No runtime feature change from a4.

## 0.1.0a4

- Upload and preview still PNG/JPEG materials up to 1 MiB and 8,388,608 pixels,
  and send their original bytes to an image-compatible model with exact source references.
- Read public HTTPS text pages with saved source URLs, reading times and partial
  coverage. Recall original observations from history after restart.
- Describe PDF reading and search as extracted-text coverage, including pages
  that mix text with images, with the original available for visual inspection.
- Stop pinning an oversized historical opening in every later context projection.
- With more than one worker, reserve capacity for user-directed work while
  keeping running work and same-context messages in order.
- Index exact consultation-source lookups as completed consultations accumulate.
  The schema v12 upgrade preserves stored records and uses the existing backup process.

## 0.1.0a3

- Report the installed runtime version consistently in Bootstrap and OpenAPI,
  matching the distribution metadata. Native autonomous practice and its weekly
  request controls remain unchanged from a2.

## 0.1.0a2

- Start autonomous practice once from Activity, with a native SELF origin,
  focused Work, shared weekly requests, source-linked results and restart-safe pause.

- Add an opt-in weekly allowance for autonomous model requests in Activity.
  Retries and uncertain sends remain counted across restarts; direct user
  messages stay usable while background events wait for more allowance.
- Complete document rewrites use a 1 MiB native message and action envelope,
  including approval details, with compact file passages and result previews.
  Downloads and checkpoint Undo preserve the original bytes across restarts.
- Improve access to ordinary dotfiles, history and retained command output, and
  make hosted-model configuration clearer, including Azure-compatible endpoints.

## 0.1.0a1

First local alpha with a bundled browser interface and a Python wheel install.

- Persistent Home and focused pages, linked results and source return.
- English and Simplified Chinese, streaming replies, steps, usage and Stop.
- Text/PDF materials, earlier history and original tool results.
- Workspace edits, conditional Undo, downloads and approved Docker commands.
- Background Pulse, connections, examples and lessons from corrections.

See the [release notes](docs/releases/v0.1.0a1.md) and
[installation guide](docs/install.md).
