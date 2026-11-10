# Alpha limitations

- Per Pulse runs locally for one user and listens on loopback. Account access,
  shared workspaces and cloud deployment are outside this release.
- A compatible model is configured separately. Streaming, tool calls and usage
  reporting are required. Provider compatibility and output quality depend on
  the model and service; no model is bundled.
- PDF reading and search cover extracted text. Scanned or image-only PDF pages
  need OCR elsewhere. Still PNG/JPEG images up to 32 MiB and 50 megapixels can
  be sent to a configured model that accepts image input; a model receives a
  copy scaled to fit 2048 px and 1 MiB when the original is larger.
- Long conversations retain all saved history, while each model request carries
  a bounded selection. Earlier messages and tool results can be read from history.
- Background turns have admission limits. Reported token totals can be incomplete
  when a provider omits usage, and do not set a provider billing limit.
- Model text edits apply by default; use `--ask-before-edits` for individual
  approval. Undo requires the relevant file version to match. Commands require
  a configured Docker image and individual approval.
- After an interrupted action with an uncertain result, inspect the original
  page and files before continuing. Such actions are not automatically repeated.
- Workspace images require a stopped service. The current image workflow uses
  POSIX filesystem operations; use it on Linux or macOS. Windows can run the
  workspace and file actions; stop the service before copying its data directory
  as a local backup.
- There are no account-level deletion controls or automatic private-data removal.
  Keep the workspace directory and images private; hosted models receive messages
  and the file contents that are read. See [security](../SECURITY.md).

Installation and release checks are described in [releasing](releasing.md).
