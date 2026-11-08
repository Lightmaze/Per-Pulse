# Connect a model

Per Pulse uses the inherited HTTP Chat Completions adapter. Configure an endpoint that supports streaming OpenAI-compatible Chat Completions, tool calls, and usage reporting:

```sh
export P_PULSE_CHAT_ENDPOINT='https://your-provider.example/v1/chat/completions'
export P_PULSE_CHAT_MODEL='your-model-id'
export P_PULSE_API_KEY='your-api-key'
per-pulse
```

Replace the placeholders with your provider's values. The endpoint is a complete URL, not a base URL. Credentials stay in the server environment; the browser never receives them. You can use `P_PULSE_CREDENTIAL_ENV` to name a different environment variable containing the key.

## Provider compatibility

The same Kernel and streaming Chat Completions adapter handle these provider
profiles. The default `P_PULSE_CHAT_PROFILE=auto` recognizes the official hosts;
other hosts use `compatible`. Select a profile explicitly for a proxy, keeping
the endpoint and model chosen by you:

```sh
export P_PULSE_CHAT_PROFILE='anthropic'
```

| Provider | Complete endpoint | Profile and wire behavior |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1/chat/completions` | `openai`: sends `max_completion_tokens`, including for reasoning models |
| Azure OpenAI | Your resource's complete Chat Completions URL | `openai` on `*.openai.azure.com`; accepts prompt/output filter annotations, including after the final text; keep the authentication setting below |
| Anthropic | `https://api.anthropic.com/v1/chat/completions` | `anthropic`: uses Claude's OpenAI compatibility endpoint and object-root tool schemas |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` | `gemini`: returns Google tool thought signatures with the same tool call, including after a workspace restart |
| DeepSeek | `https://api.deepseek.com/chat/completions` | `deepseek`: returns retained `reasoning_content` with assistant tool calls |
| OpenRouter | `https://openrouter.ai/api/v1/chat/completions` | `openrouter`: retains signed/encrypted `reasoning_details` for tool continuation; keep `P_PULSE_PROVIDER_NAMESPACE=openrouter` for its usage lookup |
| Other compatible or local servers | Your complete Chat Completions URL | `compatible`: sends `max_tokens`; use an explicit provider profile when the server needs its conventions |

Set `P_PULSE_CREDENTIAL_ENV` to `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, or `DEEPSEEK_API_KEY` to reuse that provider's server-side
environment key. Profiles never change the destination, select a model, or
copy credentials between providers. No sampling parameters or forced tool
choice are added. Per Pulse uses best-effort function calling and validates file
arguments locally before an edit or approval: supply exactly one of `content`
and `patch`. Tool schemas avoid root `oneOf`, `anyOf`, and `allOf`, which
Anthropic rejects. Streaming accepts ordinary `usage: null` chunks and records
the service's final usage report.

These settings target the providers' **Chat Completions compatibility APIs**.
Use `/chat/completions`, rather than the native Anthropic `/messages`, Gemini
`generateContent`, or OpenAI `/responses` routes. `doctor` remains a local
configuration check. Verify a selected model by writing a small file in a
fresh Work, continuing after the tool result, then reopening that Work after
restart. A proxy or model may support a different subset of the API.

For a repeatable live check from a source checkout with development dependencies,
using your configured endpoint, run:

```sh
python -m pytest -m real -s tests/per_pulse/test_provider_compatibility.py::test_configured_provider_writes_a_file_and_continues
```

It makes at most two model requests, with retries disabled, and writes only
inside a temporary test workspace. It checks the full native tool catalog,
a real file effect, the next model reply and reported usage. Ordinary test
runs exclude this paid-provider check.

Protocol references: [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling),
[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[Anthropic compatibility](https://platform.claude.com/docs/en/api/openai-sdk),
[Gemini compatibility](https://ai.google.dev/gemini-api/docs/openai),
[Gemini thought signatures](https://ai.google.dev/gemini-api/docs/thought-signatures),
and [DeepSeek thinking and tool calls](https://api-docs.deepseek.com/guides/thinking_mode/).
Azure annotations follow [Microsoft's streaming examples](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/content-streaming);
OpenRouter tool continuation follows its [reasoning format](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
and [official SDK accumulation](https://github.com/OpenRouterTeam/ai-sdk-provider/blob/main/src/chat/index.ts).

## Authentication and local servers

For Azure OpenAI Chat Completions API-key endpoints, select the complete
versioned HTTPS endpoint, keep the key in the environment and choose the
provider's `api-key` header:

```sh
export P_PULSE_CHAT_ENDPOINT='https://your-resource.openai.azure.com/openai/deployments/your-deployment/chat/completions?api-version=2024-10-21'
export P_PULSE_CHAT_MODEL='your-deployment'
export P_PULSE_API_KEY='your-azure-api-key'
export P_PULSE_CHAT_AUTH_STYLE='api-key'
per-pulse doctor
per-pulse
```

Use the version supported by your deployment. The current Azure OpenAI v1
Chat Completions URL can also be used without a version query. The optional
query permits only a non-secret `api-version` parameter; do not put keys in
URLs. Authentication defaults to `Authorization: Bearer ...` for ordinary
providers, and `P_PULSE_CHAT_AUTH_STYLE=api-key` switches to `api-key: ...`
without changing the stored credential location. This is transport support;
actual remote model compatibility is determined by the service you connect.

For Windows PowerShell, use `$env:P_PULSE_CHAT_ENDPOINT = '...'`, `$env:P_PULSE_CHAT_MODEL = '...'`, and `$env:P_PULSE_API_KEY = '...'` in the **same PowerShell session** before running `per-pulse doctor` and `per-pulse`. When disconnected, **Settings → How to connect a model** now provides a POSIX or PowerShell terminal template and a copy control. It contains only placeholders; never enter your real API key into the browser. The local `doctor` preflight sends no provider request and cannot verify remote connectivity, model behavior, or spending limits.

An **optional credential-free local model** can use the same real Kernel/World path with an explicitly operator-configured loopback HTTP Chat Completions server. Start a model server on your own computer first; no model is installed by Per Pulse. For a compatible server exposing the example port (change the port/model to match your server):

```sh
export P_PULSE_CHAT_ENDPOINT='http://127.0.0.1:11434/v1/chat/completions'
export P_PULSE_CHAT_MODEL='your-local-model-id'
export P_PULSE_ALLOW_LOCAL_HTTP='1'
export P_PULSE_LOCAL_NO_AUTH='1'
per-pulse doctor
per-pulse
```

On Windows PowerShell use `$env:NAME = 'value'` for each environment assignment. The disconnected **Settings → How to connect a model** also provides both terminal variants in the *Model server on this computer* option. Credential-free mode is refused unless both flags are set and the endpoint is explicitly loopback HTTP; it never bypasses credentials for remote HTTPS providers. Requests have no `Authorization` header in this mode. It remains one native Kernel and one World; no additional proxy or model runtime is created.

### Connect to a model on another computer in your LAN

A trusted model server on a different computer can share the same native
World/Kernel experience path. Start an OpenAI-compatible Chat Completions
server on that computer, then set its **literal private IP** and actual port
on the computer running Per Pulse:

```sh
export P_PULSE_CHAT_ENDPOINT='http://192.168.1.24:11434/v1/chat/completions'
export P_PULSE_CHAT_MODEL='your-lan-model-id'
export P_PULSE_ALLOW_PRIVATE_HTTP='1'
export P_PULSE_LOCAL_NO_AUTH='1'
per-pulse doctor
per-pulse
```

Replace the example IP, port and model ID. Settings offers the same terminal
template in English and Chinese for POSIX and PowerShell. Explicit opt-in
accepts only literal private IPv4 (10/8, 172.16/12, 192.168/16) and local
IPv6 ULA (fc00::/7) addresses; hostnames, public, link-local and loopback IPs
are not unlocked by this flag. The existing `P_PULSE_ALLOW_LOCAL_HTTP=1`
switch separately controls loopback.

LAN HTTP is unencrypted. Your model request text, images and tool context
travel to the selected computer, so choose a server and network you trust.
Credentials are not silently disabled: `P_PULSE_LOCAL_NO_AUTH=1` is a
separate opt-in for servers that require no API key. Omit it and supply an
API key for a LAN server that authenticates. Per Pulse stays bound to the
local browser loopback; this enables outbound model calls, not remote browser
access. `per-pulse doctor` confirms configuration only, without connecting
or judging the remote model.

`doctor` checks only the local configuration, **not** whether the local server is running, supports streaming/tool calls/usage, or provides good judgments. Local models may not implement the required OpenAI-compatible SSE and usage fields; incompatible responses fail normally, with no scripted fallback or learning claims. Per Pulse's background token budget remains an admission envelope rather than a hard GPU/billing cap. Run a real Work and inspect its outcome before depending on it for later actions.

If neither endpoint nor model is set, the workspace runs disconnected. Setting only one is a configuration error. Restart after changing provider configuration. Queued input from the ordinary workspace can be processed after connecting a model, so use a separate data directory when you want a fresh conversation.

Before starting a provider-backed Work or a paid posture experiment, check the
**local configuration** without sending any network request:

```sh
per-pulse doctor
per-pulse doctor --json
```

This uses the same adapter configuration validation and local credential-presence
check as the runtime. It distinguishes intentional disconnected mode, incomplete
settings, missing credentials, and locally configured settings; no secret value,
model response or source conversation is printed. The JSON form is suitable for
a local executor's preflight. A locally configured result is **not** a remote
connectivity, authentication, streaming/tool-call, usage-reporting, model-quality
or money-cap verification. The doctor sends zero provider requests, leaves the
World untouched, and returns a nonzero exit code for invalid/missing settings.

Provider options inherited from Pulse include `P_PULSE_CHAT_TIMEOUT`, `P_PULSE_MAX_OUTPUT_TOKENS`, `P_PULSE_REASONING_FIELD=reasoning_content`, and `P_PULSE_PROVIDER_NAMESPACE=openrouter`. Per Pulse also enforces a default **8 MiB maximum serialized request body** at the outbound Chat Completions boundary, configurable with `P_PULSE_MAX_REQUEST_BYTES`. This rejects oversized requests *before network send*, including those arising from long history, side computations, tool schemas or image parts. It never silently truncates the saved context; a blocked turn remains visible for review. This byte guard is not a provider bill cap. Provider compatibility must be verified separately.

**Long replies.** A reply may stream for as long as bytes keep arriving. `P_PULSE_CHAT_TIMEOUT` (default 120 seconds) is the longest wait for the next bytes, including the first; `P_PULSE_CHAT_MAX_SECONDS` (default 1800) bounds one reply's whole stream. A reply that reaches `P_PULSE_MAX_OUTPUT_TOKENS` (default 4096) is kept and ends with "[Stopped at the reply length limit. Ask to continue for the rest.]". Raise the limit if your model allows longer replies; some services reject values above their model's maximum.

**Busy or briefly failing services.** If the model service is busy (HTTP 429), has an error (5xx), reports a request timeout (408) or conflict (409), or the connection fails before any reply, Per Pulse sends the same request again, up to `P_PULSE_CHAT_RETRIES` times (default 2). It waits about half a second, then about a second, or as long as the service's `Retry-After` header asks, up to a minute. Stop ends the wait. A reply that has started is never sent again, and a request the service accepted but then stopped answering (no bytes within `P_PULSE_CHAT_TIMEOUT`) is not repeated. Set `P_PULSE_CHAT_RETRIES=0` to send each request once.

**Keys and passwords in your content.** Messages you send and files the model reads or edits are passed to your model as they are, including any keys or passwords in them, as other agent tools do. With a hosted model, the provider receives them; use a local model or keep such values out of the workspace if that matters to you. Your provider's own API key stays in the server environment and is never part of a conversation.

**Long conversations.** Each request selects from the page's saved history using
an estimated `P_PULSE_CONTEXT_TOKENS` budget (default 96000). It keeps the most
recent whole turns and retains the opening message when it fits beside the
latest exchange. An oversized old opening can be left out of that request;
earlier messages remain available through the history tools, and nothing is
deleted from the page. The current exchange stays intact and may still exceed
the budget. Set the budget below your model's context window, for example
`P_PULSE_CONTEXT_TOKENS=12000` for a local model served with a 16K context.

**Tokens used.** Under a page's title, Per Pulse shows the tokens the model service reported for that page, including background work on it. Open it for the tokens sent, the tokens served from cache, the tokens written, and the latest turn's total. Only reported usage is counted. If the service reports none for a call, the total says "at least" and names how many calls are missing. For cost, apply your provider's rates to these numbers.
