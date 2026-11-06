# Install Per Pulse

You need Python 3.12 or later. The release wheel includes the built interface;
Node.js and Git are needed only when developing from source.

## Download and install

Download `per_pulse-0.1.0a1-py3-none-any.whl` from the
[v0.1.0a1 release](https://github.com/Lightmaze/Per-Pulse/releases/tag/v0.1.0a1).
In the directory containing the download, create an environment and install it.

Linux or macOS:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./per_pulse-0.1.0a1-py3-none-any.whl
per-pulse --example
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\per_pulse-0.1.0a1-py3-none-any.whl
.\.venv\Scripts\per-pulse.exe --example
```

Open **http://127.0.0.1:8000**. The example contains sample conversations and
linked pages; it runs without a model. Stop the server with Ctrl+C. To start
your own workspace, run `per-pulse` without `--example` (on Windows use
`.\.venv\Scripts\per-pulse.exe`). Your ordinary workspace and the example are
kept separately.

## Have your first conversation

[Connect a model](models.md), run `per-pulse doctor`, then start `per-pulse`.
You can use a hosted Chat Completions endpoint or a model server on this computer.
You need a model that supports streaming, tool calls and usage reporting.

1. Send a message in Home.
2. Create a page for a focused part of the work, and continue there.
3. Select a result and bring it Home. Follow its source link to return to the
   original page and message.
4. Stop and restart the service; your pages and messages remain available.

Without a model, new messages stay queued. Connecting a model and restarting
can process those messages. To start fresh instead, use a new directory:

```sh
per-pulse --data-dir ./my-first-workspace
```

The interface language can be changed in Settings. With a model connected,
pages can continue in the background, up to 12 turns per hour by default.
Use `--pulse-budget N` to change this or `--no-pulse` to switch it off. Model
file changes apply by default and offer Undo; `--ask-before-edits` asks first.
See [workspace actions](workspace-actions.md) for what Undo can restore.

## Start autonomous practice

For self-directed work, open Activity, choose a weekly request limit and select
**Start autonomous practice**. See [autonomous practice](autonomous-practice.md)
for pause, usage and restart behavior.

## Update and keep your work

Stop the service before upgrading, then install the new wheel in the same
environment. Your workspace is outside the installation, in
`~/.local/share/per-pulse` by default, or your chosen `--data-dir`.
For a backup or a move, use [workspace images](image.md) while stopped.
There is no in-place data downgrade guarantee; keep a backup before an upgrade.

## Check the download

Download `SHA256SUMS` from the same release. On Linux run `sha256sum -c SHA256SUMS`
from the download directory (both archives must be present). On macOS run
`shasum -a 256 -c SHA256SUMS`. On Windows compare the wheel's entry with:

```powershell
Get-FileHash .\per_pulse-0.1.0a1-py3-none-any.whl -Algorithm SHA256
```

## If startup fails

- **Python is too old:** check `python --version` (or `py -3.12 --version` on
  Windows) and create the environment with Python 3.12 or later.
- **Port 8000 is busy:** run `per-pulse --port 8001` and open the printed URL.
- **AI disconnected:** use Settings → How to connect a model, or follow the
  [model connection guide](models.md); restart after setting variables.
- **The wheel cannot be installed:** run `python -m pip check` and include the
  error in a [bug report](https://github.com/Lightmaze/Per-Pulse/issues), with
  your Python version and operating system. Remove credentials and private text.

See [known limitations](known-limitations.md) for alpha boundaries.
