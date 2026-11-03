# Connect a model

Per Pulse uses the inherited HTTP Chat Completions adapter. Configure an endpoint that supports streaming OpenAI-compatible Chat Completions, tool calls, and usage reporting:

```sh
export P_PULSE_CHAT_ENDPOINT='https://your-provider.example/v1/chat/completions'
export P_PULSE_CHAT_MODEL='your-model-id'
export P_PULSE_API_KEY='your-api-key'
per-pulse
```

Replace the placeholders with your provider's values. The endpoint is a complete URL, not a base URL. Credentials stay in the server environment; the browser never receives them. You can use `P_PULSE_CREDENTIAL_ENV` to name a different environment variable containing the key.

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

Provider options inherited from Pulse include `P_PULSE_CHAT_TIMEOUT`, `P_PULSE_MAX_OUTPUT_TOKENS`, `P_PULSE_REASONING_FIELD=reasoning_content`, and `P_PULSE_PROVIDER_NAMESPACE=openrouter`. Per Pulse also enforces a default **8 MiB maximum serialized request body** at the outbound Chat Completions boundary, configurable with `P_PULSE_MAX_REQUEST_BYTES`. This rejects oversized requests *before network send*, including those arising from long history, side computations, tool schemas or image parts. It never silently truncates the saved context; a blocked turn remains visible for review. This byte guard is **not** a context-token limit, provider bill cap, or solution to automatic long-history management. Provider compatibility must be verified separately.
