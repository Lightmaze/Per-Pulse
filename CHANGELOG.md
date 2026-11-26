# Changelog

## Unreleased

## 0.1.0a39

- Let delivery review revise a prepared Work report before Home receives it (#366).
- Keep the latest user purpose in the expert working view and task-relevant experience selection, including long instructions (#367, #368).
- Resume held autonomous work from its original durable receipt without replacing the user's purpose; preserve first Work handoffs across local round boundaries (#369–371).
- Return to the original reading position and retain Work creation drafts through startup, while binding drafts and pending creation intent to the observed World (#372–375).
- Reduce growing native history validation, event append, page projection and foreground admission costs; retain access to older unsettled turns (#376–380).
- Exercise two consecutive native Pulse activations with foreground sourced file deliveries, pause, durable budget and cold restarts (#381, #382).
- Continue typing immediately after attaching files or inserting page references, preserving the draft and caret and keeping delayed acknowledgements in their original Work (#383).

## 0.1.0a38

- Keep historical Work file results responsive in long-lived Worlds with a native SQLite action index that also opens existing databases (#362).
- Discover original file actions and exact versions from another Work's history, even after the original conversation leaves model context (#363).
- Return a derived Work file to Home alongside the verified original file it read before producing that result; open and download each independently, preserve source navigation across restart, and invalidate an undone source without deleting the derived file (#364).

## 0.1.0a37

- Return files completed in earlier Work turns to Home after SQLite restart or committed subject succession using the original verified Kernel file action, without writing again (#357).
- Let Work report an earlier completed file by its exact workspace path when the old action ID has left model context; preserve Work ownership, byte integrity and Undo (#358).
- Keep task-relevant earlier shared corrections available for both Work pre-action decisions and final deliverable review after long histories and restart (#359).
- Exercise one persisted browser journey through Work A correction, Work B's approved real file, later path-only Home report, source return, original-byte download, restart and Undo, including installed-wheel acceptance (#360).

## 0.1.0a36

- Keep older, still-applicable Work experiences in the main model decision and later reuse after a long SQLite history; verify their exact revisions and withdrawal instead of rejecting them because they are absent from a recent candidate list (#352).
- Bound the per-decision omission material for long-lived Worlds while retaining accurate omitted counts and explicit complete history queries (#353).
- Return actual Work file results to Home with their native action source and version, including post-restart preview and Undo invalidation (#354).
- Download the original verified Work file directly from Home, preserving bytes and source identity across restart and withdrawing the link after Undo (#355).

## 0.1.0a35

- Recover native Work creation after lost HTTP receipts, including a browser-tab close, without re-admitting the original first task (#340, #341).
- Preserve ordinary sourced Send and new Work-to-Work Send provenance across lost acknowledgements and restart (#342, #345).
- Keep an existing Work's actual task purpose visible when long conversation history is compacted (#343).
- Revalidate a selected Work experience when the source changes while expert computation is in flight (#344).
- Restore a source-bearing file Return Home from its accepted native receipt after the network acknowledgement is lost, without replaying a changed file (#346).
- Keep /tick completion status consistent with the final visible assistant reply (#347).
- Resume selected-result transfers and Return Home after browser reload using the same native request identity, without a duplicate effect (#348).
- Offer bounded older task-matching lessons to the ordinary later Work pre-action decision in English and Chinese, keeping World scope and withdrawal semantics (#349, #350).

## 0.1.0a34

- Restore the verified selected message and its actual Work task when earlier native context has been omitted, without copying the source Work's private history (#330).
- Recover an ordinary Home or Work Send after a lost HTTP response or restart without re-admitting the same native user event or issuing duplicate downstream work (#331).
- Keep the last instruction of a long, source-heavy Work visible to the pre-action expert under its existing bounded work-material budget (#332).
- Give the native pre-action expert the current verified Work source and replace stale source advice when the owner revises the lesson (#333).
- Recover a temporarily SQLite-contended native runtime lease renewal without treating that transient lock as permanent shutdown (#334).
- Keep a long user's final Work requirement visible in bounded main-model history while retaining the complete original message behind `read_history` (#335).
- Verify restored file contents or absence through workspace-anchored paths when an Undo receipt was lost; a replaced symlink cannot make an outside path look restored (#336).
- Bind Work-to-Home source identity to the originally admitted native CausalLedger message, so a different Work cannot claim a lost-response retry after restart (#337).
- Keep an undone file-producing action withdrawn as a cross-Work source even if identical bytes reappear at the same path; allow explicit selection of the new current file instead (#338).

## 0.1.0a33

- Keep revised Work experiences reachable through native `posture.list/find` cursor pages even when earlier records change; continue to fetch their latest revision (#324).
- Let a later Work read an entire saved correction using native `posture.get` and existing versioned `detail.next` fragments, including long lessons and decision shifts (#325).
- Keep one Work-to-Work result transfer when the receiving World accepted the message but the browser lost its response. A retry keeps the original source and does not start duplicate model work (#326).
- Create a new sourced Work directly from a selected, editable completed result in another Work, retaining the original result anchor, the new instruction, drafts and Back navigation (#327).
- Keep one sourced Work-to-Home result return after the same lost-response condition, without a second native user stimulus or repeated downstream effects (#328).

## 0.1.0a32

- Keep all older Works reachable while new activity arrives between native `work.list` or title-search pages. Discovery now uses a stable creation-order cursor; the UI still shows recent activity (#320).
- Preserve Home's current route, unsent draft and keyboard focus when the server has created another Work but its HTTP acknowledgement arrives after the person navigated back. The new Work remains saved and source-linked (#321).
- Restore an explicitly selected, still-shared Work experience from the native Posture journal when the Kernel actually omits early history for a later model decision. Keep revised, withdrawn, and unavailable-source checks and the bounded full-revision reading path (#322).
- Make Web multi-address timeout fallback regression deterministic on CI without changing production HTTP behavior (#319).

## 0.1.0a31

- If creating a new Work from a verified file fails before its first task is admitted, remove the unstarted Work and its temporary material receipt transactionally. Keep the source file and producing action, including across SQLite restart (#315).
- Recover a Home-created Work after an uncertain first event admission: distinguish a refused task from a committed task whose acknowledgement was lost, avoiding both orphaned Works and duplicate execution (#316).
- Keep bounded first and last portions of a later, long revision of a selected Work experience visible before the receiving Work's next model decision. Mark the preview incomplete and retain native full-revision retrieval (#317).

## 0.1.0a30

- Start a new Work directly from a verified Home or Work file result. Prepare the original Kernel file receipt and exact source material before the new Work's first model stimulus; then read that file, produce a new artifact under ordinary approval, and retain both through SQLite restart (#313).

## 0.1.0a29

- Move a successfully Home-created file into an existing Work's unsent draft with its producing Kernel action, source link and exact file digest verified; continue the file under ordinary actions, return the result to Home and recover its source after SQLite restart (#311).

## 0.1.0a28

- Let a later Work read-only inspect and search its subject's earlier Works directly, retaining the native Work/Kernel ownership and file-action boundaries (#307).
- Find an original decision by content across owned Works even when its Work title is unknown, returning a compact, source-addressed quote and history page cursor from the same World/SQLite (#308).
- Return a later Work's selected result to Home with verified original evidence from another Work, checking the original event and verbatim excerpt at request preparation and delivery (#309).

## 0.1.0a27

- Run the complete saved-correction → next Work → approved file → sourced Home return → restart journey against an isolated cold-installed wheel, not the source checkout (#302).
- Search and page through owned historical Work messages from Home, retaining original event identity and SQLite persistence (#303).
- Return the exact continuous match from a long historical message, including original character offsets and truncation status (#304).
- Attach a World-verified original Work event and exact quote when creating or continuing another Work, checking source ownership and text again before native delivery (#305).

## 0.1.0a26

- Start a new Work from an explicitly selected, saved correction in its original Work, carrying its verified revision, applicability and source attribution into the next task while leaving the private source conversation in place (#298).
- Recognize in the new Work when that selected experience has since been revised, paused, kept local or made unavailable. Link back to the original Work for the current source instead of treating an old snapshot as fresh guidance (#299).
- Recheck the selected experience against the same World and Posture journal at each later Kernel model decision, including after approval. Supply revised shared content or withdrawn-source status without silently rewriting old messages, files, or effect permissions (#300).

## 0.1.0a25

- Preserve plain text dropped together with supported image material in the same Home or Work draft, including Work/Home return and refresh; send both only on explicit Send (#294).
- Make up to two recently omitted original user messages recognizable in bounded long-context model requests with exact historical message IDs and `read_history` addresses, without changing the saved transcript or action authority (#295).
- When a prior source-backed Send succeeds after the user has started the next draft, detach only confirmed sent material IDs while retaining the new draft text and attachments; failed sends remain recoverable (#296).

## 0.1.0a24

- Preserve task text and supported images when pasted together into the same Home or Work draft. Keep both across refresh and send them together after explicit Send (#291).
- Pause autonomous practice without overwriting a newer weekly request allowance saved from another view. Keep the current persisted allowance and require an explicit value when enabling practice again (#292).

## 0.1.0a23

- Find exact phrases beyond the first passage of public webpages, structured research responses and selectable PDF text with source-version checks and follow-on matches. Retain bounded excerpts, observed coverage and page attribution (#287).
- Search an existing saved Work observation without fetching the live page again, use an exact continuous passage in the existing experience editor, and carry the sourced lesson into later Works and Home after restart (#288).
- Pause autonomous practice even when the weekly-limit field has an unsaved or invalid draft. Keep the persisted allowance unchanged and preserve the existing SELF/World identity and consumption history (#289).

## 0.1.0a22

- Read public structured HTTPS research responses (JSON/JSON-LD, XML/Atom/RSS, CSV and Markdown) through the native sourced `read_web` tool, with original bounded observations, provenance, pagination and restart history (#284).
- Read selectable text from public HTTPS PDFs with page markers, original PDF hash, bounded extraction and explicit incomplete/scanned-page status. Inspect the exact prior observation from its Work after restart (#285).

## 0.1.0a21

- Select multiple text, PDF, or PNG/JPEG files at once in the Materials system picker and attach them to one unsent Home/Work draft. The original source files and draft persist across refresh, with a five-attachment limit and partial-success preservation (#281).
- Show the number of confirmed attachments while the rest of a selected batch uploads. Preserve confirmed sources and keep the native UI/browser completion checks for delayed uploads (#282).

## 0.1.0a20

- Select an exact passage directly in a saved HTTPS webpage observation and place it in that Work's existing experience editor. Keep the original observation and source Work, the 8 KiB excerpt bound, and the normal later-Work handoff (#278).
- Preserve ordinary text pasting in Home and Work when a clipboard also contains unsupported files. Show a file notice without cancelling the text paste; keep supported attachments on their existing durable draft path (#279).

## 0.1.0a19

- Reconsider up to 16 older, compact, still-authorized experiences alongside the newest 128 full experience candidates before a later Work decision. Keep original subject/source/feedback filtering, omission tracking and the same World-owned journal (#274).
- When an HTTPS webpage body times out or disconnects after delivering usable text, retain the observed prefix as an explicitly partial source rather than discarding it or claiming a complete read (#275).
- Read public gzip-compressed HTTPS webpages with bounded input and decompressed output (512 KiB each). Carry the original observation through the existing Work, Home and restart/source path; incomplete compressed streams remain marked as partial observations (#276).

## 0.1.0a18

- Start a new Work directly from an operator-saved native file reading or command-output passage, with the original source Work, observation type and test-selector evidence carried forward. It does not re-read the file or re-run the command (#269).
- Keep earlier user corrections and completed decisions in long model sessions when an old tool output is too large: replace the in-context output with a durable, paged `read_history` reference instead of discarding its entire turn (#270).
- From Home, browse and reopen older completed Work results after they fall outside the latest activity window. Already-visited results remain findable; the original result is still linked to its source Work (#271).
- Limit automatic pre-decision experience-body reads to recent SQL candidates while retaining earlier visible experience IDs and revisions for omission and stale-decision checks; explicit historical lookup remains available (#272).
- Preserve the single World/Kernel/SQLite and original source and permission boundaries throughout these additions.

## 0.1.0a17

- Continue directly from a saved webpage observation in Work A to a new user-authored Work, with its original source retained and the existing Home/Work return path; no new session store or automatic source copying (#263).
- Keep webpage excerpt editing consistent with the server save: prevent concurrent changes while the save is pending, and ignore stale responses from an earlier editor instance (#264).
- Put the precise, persisted, operator-selected webpage experience into Work B's first native request as attributed historical data. The World verifies its source, state and sharing scope; no private source URL or entire transcript is copied (#265).
- Continue long workspace directory browsing from the last visible file or folder after an intervening change. Explicitly restart when the anchor disappears; reject forged protected-name and symlink anchors (#266).
- After a restart, open a saved native webpage example from its original Work's Lessons and examples and create a further Work without saving or fetching the page again (#267).
- These are source, persistence, navigation and file-operation improvements. Browser-model responses in automated tests are explicit fixtures, not proof that a real model learned or improved its choices.

## 0.1.0a16

- Inspect the exact text previously delivered by a native public HTTPS `read_web` call from a Work's Web sources, including the original URL, observation time, extracted-text range and digest. The saved observation survives later webpage changes and service restart; inspecting it does not fetch the page again (#259).
- Select an exact passage from that saved webpage observation and save it as an operator-authored example in the existing World-owned Posture journal. It becomes eligible for later Works while retaining a source path; saving remains possible with the provider disconnected and does not copy the original private Work history (#260).
- Carry that example into a later Work's existing pre-action expert material with explicit `native_web_example` provenance and an unverified-past-observation status. Source-only URLs and receipts stay private. This makes the input attributable; it does not prove a live model used the example or improved its decision (#261).

## 0.1.0a15

- Retain an exact passage from an actually completed native HTTPS `read_web` observation as an existing Posture example, with the observed URL, text version and source Work. Later Works can use that example without copying the original private conversation (#256).
- Continue native workspace browsing past the first batch of files. `browse_workspace` returns a bounded `nextCursor` and reports changed directories rather than silently losing later entries (#255).
- Browse older verified file results in a long-lived Work. The UI loads earlier Kernel receipts on demand and keeps the original file version, download, handoff and conditional Undo behavior (#257).
- Complete Chinese wording for source-to-Work and material recovery flows, so those paths use the same accessible language as the rest of the UI (#254).

## 0.1.0a14

- Drop or paste several PNG/JPEG, text and PDF files together into a Home or Work composer. Keep up to five source-backed attachments in the unsent draft through refresh; only explicit Send calls the model. Preserve successfully attached files and explain what stopped the rest (#250).
- Store long-term material receipts in indexed rows of the same World SQLite rather than an ever-growing page JSON field. Migrate old receipts transactionally on opening the World, preserve source IDs, file bytes and hashes, and remove the World-lifetime 1,024-receipt ceiling (#251).

## 0.1.0a13

- Help later Works discover shared Habitat regions without being told their names: `habitat_observe(list)` lists the World regions, `habitat_observe(find)` searches across them with source-linked excerpts and explicit incomplete-search continuation, and a bounded region index appears before ordinary model decisions (#245, #246, #247).
- Protect Habitat changes made from an older read: `habitat_write` can verify `fullCurrentSha256` at execution time, including after one-time approval. A stale edit reports the version change and directs the Work to reread instead of overwriting newer work (#244).
- Let Activity close while a native weekly-allowance update is pending, so the Home source link remains usable. The already requested pause continues and can be confirmed against the native weekly state (#248).
- Habitat changes still require Allow once and have no Undo. The region index and tools create an opportunity to revisit old work; live-provider autonomous retrieval and improved later choices remain untested.

## 0.1.0a12

- Visit a shared native Habitat region from Home or separate Works. A Work can write a file with the original Kernel action's one-time approval; a later Work can read and append to that same physical file, and Home can revisit it after restart, with verified writer provenance (#181).
- Continue a Habitat text file beyond its initial 64,000-character observation through pinned `nextOffset`/`expectedSha256` reads. Stop if the file changes instead of joining pages from different versions (#242).
- When only Pulse's Home observation fails, keep review of currently verified Works available rather than telling the person to refresh before taking any action; unverified background results stay unavailable (#241).
- Habitat region edits currently require explicit Allow once and have no Undo; normal Workspace file edits keep their existing auto-apply and Undo behavior.

## 0.1.0a11

- Keep oversized tool results readable during the same Work turn through saved, source-linked history pages; a later context eviction still points to the original result without replaying the tool action (#238).
- Keep oversized saved user input readable in the same turn through a clearly partial preview and exact `read_history(role="user")` pages, without asking the user to repost it (#239).
- Explain a background turn stopped by the weekly allowance, or one whose start could not be confirmed, with a next step (Activity, or send a message) instead of showing only its code.
- Let the model correct a mistaken tool call. An unknown tool name or arguments that are not one JSON object no longer end the turn; like any failed call, the model is told why (and which tools are available) instead of receiving only an error code.
- Attach full-size phone photos and screenshots (PNG/JPEG up to 32 MiB and 50 megapixels) instead of refusing anything over 1 MiB or 8 megapixels. The original is kept and downloadable; a model receives it unchanged when it fits 1 MiB and 2048 px, otherwise an upright scaled copy, and is told so. The Materials preview shows that same copy.
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
