# Install Per Pulse

You need Python 3.12 or later. The release wheel includes the built interface;
Node.js and Git are needed only when developing from source.

## Download and install

Download `per_pulse-0.1.0a26-py3-none-any.whl` from the
[v0.1.0a26 release](https://github.com/Lightmaze/Per-Pulse/releases/tag/v0.1.0a26).
In the directory containing the download, create an environment and install it.

Linux or macOS:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./per_pulse-0.1.0a26-py3-none-any.whl
per-pulse --example
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\per_pulse-0.1.0a26-py3-none-any.whl
.\.venv\Scripts\per-pulse.exe --example
```

Open **http://127.0.0.1:8000**. The example contains sample conversations and
linked pages; it runs without a model. Stop the server with Ctrl+C. To start
your own workspace, run `per-pulse` without `--example` (on Windows use
`.\.venv\Scripts\per-pulse.exe`). Your ordinary workspace and the example are
kept separately.

## Have your first conversation

[Connect a model](models.md), run `per-pulse doctor`, then start `per-pulse`.
You can use a hosted Chat Completions endpoint or a model server on this computer
or another computer at a private LAN IP. LAN HTTP requires an explicit opt-in;
it sends the model task and file context without encryption.
You need a model that supports streaming, tool calls and usage reporting.

1. Send a message in Home.
2. Create a page for a focused part of the work, and continue there.
3. Select a result and bring it Home. Follow its source link to return to the
   original page and message.
4. Stop and restart the service; your pages and messages remain available.

Drag several text, PDF or PNG/JPEG files onto the Home or Work composer, or
choose several at once in the Materials file picker, to attach them to an unsent draft
(up to five total). The picker shows confirmed progress during a batch; you can
refresh before Send.
Existing drafts and successfully attached files remain after a partial failure.
Pasting or dropping supported images and text together keeps both in the draft,
including after refresh.
Open **Materials** to browse and preview them. Images need
a model that accepts image input; PDF reading covers extracted text. You can
also ask a configured model to read public HTTPS pages, structured JSON/XML/CSV
research sources or selectable paper PDF text, and return to the saved source
observation. See [Materials](materials.md).

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

## Continue from a saved correction

In a Work with a configured model, correct a mistaken approach and open that
Work's saved experiences. When an active correction is available, select
**Start a Work from this saved experience** and enter the next task. The new
Work keeps a link to the source, its selected revision and the specific lesson;
it does not copy the entire source conversation. You can inspect its source
status and return to the original Work.

If you revise, pause or keep the original experience local, the later Work
will show that change. On subsequent ordinary model requests, Per Pulse checks
the original World-owned record again and passes the current shared revision or
the withdrawn/unavailable status as attributed source data. This does not send
a new user request or approve a file change. Workspace actions continue to use
the existing Allow once or Undo controls. Without a configured model, you can
still inspect the saved source, but no new AI action runs.

## Continue in a shared Habitat region

With a configured model, Home and focused Works can revisit the same Habitat
regions using the native `habitat_observe` and `habitat_write` tools. Later Works
can list regions without a name, search saved regional file contents with
`habitat_observe(find)`, and read exact source passages with version-pinned offsets.
Ordinary model requests receive a bounded directory of existing region names,
so a later Work can decide whether earlier work is relevant. Ask one
Work to write a regional text file, approve that exact action with **Allow once**,
then ask another Work to revisit and continue it. Home can read the same file
after restart. For a file longer than the first 64,000 characters, the model can
use `nextOffset` with `fullCurrentSha256` to read the remainder without mixing
versions. Pass a file's `fullCurrentSha256` to a later write to avoid replacing a newer
version after another Work has edited it; read the current file again after a
version conflict. Habitat changes still have no Undo and always need Allow once;
regular workspace edits still have their normal Undo.

## Start autonomous practice

For self-directed work, open Activity, choose a weekly request limit and select
**Start autonomous practice**. The current wheel includes this entry point;
previously published a1 wheels do not. See [autonomous practice](autonomous-practice.md)
for pause, usage and restart behavior.

## Update and keep your work

Your workspace is outside the installation, in
`~/.local/share/per-pulse` by default, or your chosen `--data-dir`.
Before upgrading, stop the service and copy the complete data directory to a
backup, then install the new wheel in the same environment.
A first upgrade from a version older than a7 migrates the World database to
schema v13 for the durable experience journal and first saves a database backup.
Upgrading from a7 or later keeps that schema. Existing experience is imported on its first ordinary update, retaining the
original components, sources and revision history. In a14, old material receipts
stored in page JSON move transactionally into indexed rows of the same World
SQLite database when the World first opens; the original workspace files and
material IDs remain in place. Keep the pre-upgrade backup, as downgrading to
older builds is not supported. For a move, see
[workspace images](image.md); restore an older image with its matching
implementation before upgrading the restored directory.
There is no in-place data downgrade guarantee; keep a backup before an upgrade.

## Check the download

Download `SHA256SUMS` from the same release. On Linux run `sha256sum -c SHA256SUMS`
from the download directory (both archives must be present). On macOS run
`shasum -a 256 -c SHA256SUMS`. On Windows compare the wheel's entry with:

```powershell
Get-FileHash .\per_pulse-0.1.0a26-py3-none-any.whl -Algorithm SHA256
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
