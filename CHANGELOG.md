# Changelog

## Unreleased

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
