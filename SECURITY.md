# Security and private data

Per Pulse is a local, single-user service bound to loopback. Keep it on your
computer; do not expose its port through a public proxy. The service has no
account login or multi-user access controls.

Messages, files and model results persist in your data directory, by default
`~/.local/share/per-pulse`. Workspace images include that private state. Protect
both with your operating system's access controls and disk encryption, and keep
them out of source control and public issue attachments.

Your model receives messages and the contents of files it reads, including
credentials present in that content. Choose a local model or remove those values
before using a hosted model. The provider API key comes from the server
environment and is not sent to the browser.

Model text edits apply by default inside the workspace. Use `--ask-before-edits`
to approve each change or `--no-workspace-actions` to disable them. Commands
require an explicitly configured Docker image and individual approval. Inspect
outside material before allowing actions based on it.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/Lightmaze/Per-Pulse/security/advisories/new)
when available. If private reporting is unavailable, open an issue asking for a
private reporting channel without including exploit details or private data.
Ordinary bugs can be reported in public with synthetic examples.

Include the Per Pulse version, operating system, steps and expected behavior.
Remove API keys, personal conversations, workspace databases and images. Fixes
target the latest alpha; upgrade after backing up a stopped workspace.
