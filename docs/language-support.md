# Language support and maintenance

Per Pulse supports English (`en`) and Simplified Chinese (`zh-CN`). Two settings govern different parts of the experience: the language of the interface, and a preferred language for future model responses.

## User-facing contract

| Setting | Values | Default | Persistence | Takes effect |
| --- | --- | --- | --- | --- |
| Interface language | `system`, `en`, `zh-CN` | `system` | Current browser | Immediately, without reloading or remounting the workspace |
| Response language | `auto`, `en`, `zh-CN` | `auto` | Current World's component state | Next new turn |

The interface setting appears in Settings as **Language / 语言**. Language choices use recognizable native names, including **English** and **简体中文**, so a user can recover from an unfamiliar interface language. `system` follows the first supported language in the browser's ordered preferences: Chinese resolves to Simplified Chinese and English resolves to English. A list containing neither falls back to English. A lower-priority Chinese entry does not override an earlier English entry. The document's `lang` attribute follows the resolved interface language.

Response language is independent. `auto` follows the task and conversation without imposing a fixed language. `en` and `zh-CN` express a default preference, while an explicit language instruction in the current task takes priority. For example, a Chinese interface with English replies is supported, and a task asking for an English draft can still produce English while the saved preference is Chinese.

Changing either setting preserves the page, its navigation/source identity, the user's draft, and the ongoing work. It does not create a new subject or session. Changing response language does not submit a conversational message or run a model by itself. Each turn samples the preference at its first model boundary. Its later tool rounds and approval continuation keep that snapshot, including a snapshot of `auto`; the next new turn reads the latest saved choice. Product copy describes this as taking effect for the next new reply.

## Content and presentation

Translate product copy: navigation, buttons, form labels, placeholders, accessible names, empty states, status explanations, confirmation notices, and recognized error messages. Keep English and Chinese entries together in the shared catalog so callers use stable keys rather than scattered string replacements. Keep variable values out of translation keys and interpolate them as text.

Keep authored material intact: messages, user page names, page instructions, reference labels, source excerpts, files, and learned experiences. A language change never rewrites durable history. Authored example notes remain in their original English. A page named `Home` by the user remains that title; only the actual Home page is localized by its stable ID/kind. Reference labels remain associated with their original text so inline links retain their source matches.

The brand name **Per Pulse**, code, paths, tool names, IDs, API fields and structured status values remain unchanged. Localize the presentation of status codes, not the codes themselves. Known API errors can be rendered through their stable `code`; unknown diagnostics remain available in the server's original wording rather than becoming an inaccurate generic explanation.

Interface locale is not part of a page key, route, request identity or component identity. React updates text in place; do not key the application by language or reload on change. Keep locale-dependent labels out of effects that load a page or reset a draft. A text change may affect layout, but must not trigger navigation or intentional scrolling to another location.

## Storage and API

Interface preference is a local browser setting. It may be shared by tabs at the same application origin; it must never be stored in a conversation or silently change response language. Handle unavailable or malformed browser storage by using the system default while keeping the workspace usable.

Response preference uses the existing World component store, scoped by the persistent workspace identity. No separate conversation store or model loop is introduced. The ordinary workspace and the example workspace retain independent response settings. Existing worlds that predate this setting read `auto` without altering history.

The API is:

```http
GET /api/settings
```

```json
{"responseLanguage":"auto"}
```

```http
PATCH /api/settings
Content-Type: application/json
```

```json
{"responseLanguage":"zh-CN"}
```

The PATCH response is the saved settings object. Bootstrap exposes that same object under `settings`. PATCH accepts exactly the `responseLanguage` field; missing or unknown fields, `null`, non-string values and unsupported values return `422 invalid_request`. Clients must display successful persistence only after the server accepts it; failed writes must leave the previous saved choice recoverable. See [the API contract](api.md).

The native model path receives the explicit workspace preference without changing the user's message or causal payload. Preserve scope and provenance at the existing Kernel boundary, using the native durable turn-advice mechanism. The setting is sampled once per turn and remains fixed during that turn; changing it does not cancel work or alter action permissions. Existing language instructions in the task remain authoritative. Do not silently replace an `auto` snapshot with a newly selected language during an approval continuation.

## Maintaining bilingual features

When adding or changing a feature:

1. Add English and Chinese catalog entries for every product-facing label, including accessible text, validation, errors, loading and empty states.
2. Use stable keys and render user-authored values unchanged. Translate a sentence as a whole when Chinese and English need different word order.
3. Confirm that changing locale does not create network work, reset input, remount a dialog, lose keyboard focus, or invalidate the current source link.
4. Check Chinese input composition. Confirming an IME candidate must not send a message or activate a picker result. Preserve the composition flags and the `keyCode === 229` compatibility guard.
5. Check both languages on desktop and a narrow mobile viewport. Avoid truncating the distinguishing part of an action or hiding a button behind long English copy.
6. Keep docs, code identifiers and test names in English. Bilingual fixtures and label examples belong in tests and docs when they exercise the product behavior.

## Integration with the parallel routes

This language branch starts from `feat/english-session-pages`. PRs #2–#5 are separate work streams; their changes are not included merely because this contract describes the integration points.

| Route | Relevant surface | Language integration |
| --- | --- | --- |
| Core UIUX, PR #4 | Existing components, status helpers, search/dialog keyboard handling, source navigation | Keep its focus and return-position behavior, source anchors, stale-state recovery and IME handling while using the shared catalog. |
| Core Harness, PR #3 | `ExecutionStatus`, `PageArtifacts`, approval/Stop state and Settings | Translate action explanations and artifact controls. Keep action IDs, arguments, permission decisions and successful-effect receipts exact. Format displayed dates with the selected interface locale. |
| Posture engineering, PR #5 | `LearningDialog`, the learning section in Settings, older-source navigation | Translate controls and condition connectives. Preserve lesson content, corrections, source messages, feedback and revision identities. Keep both language and learning controls in Settings. |
| Pulse continuity, PR #2 | Runtime settings, background admissions and causal connections | Retain stable structured fields and the original propagated content. Language changes do not activate a pulse, change its admission or reset the continuing subject. |

These integrations need verification after the branches are combined. Separate branch checks are not evidence that the combined application has passed.

## Verification checklist

Record actual command results and browser checks with the implementation handoff. The following list describes the acceptance path; it is not a record of completed checks.

- Check equal catalog coverage, locale resolution, fallback behavior, placeholder handling, unavailable browser storage and language preference persistence.
- Verify GET/PATCH settings, invalid values, default migration, restart persistence, workspace isolation, and no new messages or provider calls from a settings write.
- Capture native model requests for `auto`, English and Chinese choices. Verify that user messages remain byte-for-byte intact, the explicit-task override is stated, a new turn observes preference changes, and later tool rounds or approval continuation retain the earlier turn's snapshot without restarting the conversation.
- In the browser, switch both directions from Settings; reload; reopen a page; confirm the selected interface and response preference. Check `document.documentElement.lang`.
- Type a bilingual draft with a linked page, switch language, visit the source and return. Confirm draft text, link identity, reading position and current page remain usable.
- Exercise an error, a loading state, an empty state, a disconnected workspace and an existing example workspace in both interface languages. Preserve unknown error diagnostics.
- Check keyboard dismissal, focus and IME composition, then repeat the main path at a narrow mobile width.
- Build the production client and verify the packaged wheel serves the updated assets.

Transport fixtures can verify language preference wiring and persistence. A live model's adherence to that preference is a separate observation and must be described only when it is actually exercised.
