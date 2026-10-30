"""Private cold Pulse Image: carry retained state, not a live process or authority.

Supported: the normal product-home layout, a cleanly released World, the same
Python implementation and explicitly selected credentials/resources on restart.
This is not a sanitized public distribution, live fork, sleep or cross-model
state equivalence. Loading never starts a runtime or executes archive contents.
"""
from __future__ import annotations

from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import tempfile
import time
import zipfile

FORMAT = 'p-pulse-cold-image.v1'
DATABASES = ('world.db', 'kernel.db', 'kernel.db.coverme.sqlite3',
             'kernel.db.expert.sqlite3', 'kernel.db.expert.sqlite3.trace',
             'kernel.db.cognition.sqlite3')
# Keep both native layouts: the product launcher supplies ``checkpoints``;
# Per Pulse uses Kernel's workspace-relative default ``workspace-checkpoints``.
# These are retained evidence, not transferable write/restore authority.
ROOTS = ('workspace', 'checkpoints', 'workspace-checkpoints')
INCOMPLETE = '.image-restore-incomplete'
MAX_ENTRIES = 4096
MAX_FILE = 256 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_MANIFEST = 2 * 1024 * 1024


class ImageError(ValueError):
    """A fixed diagnostic; never include private file or database contents."""


def _check(condition, message):
    if not condition:
        raise ImageError(message)


def _stamp(path: Path):
    s = path.lstat()
    _check(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, 'only single regular files can be captured')
    _check(s.st_size <= MAX_FILE, 'image file limit exceeded')
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode


def _path(name, *, directory=False):
    _check(isinstance(name, str) and name and '\\' not in name and '\x00' not in name,
           'invalid image path')
    p = PurePosixPath(name)
    _check(not p.is_absolute() and str(p) == name and all(x not in {'.', '..'} for x in p.parts),
           'invalid image path')
    valid = (name in ROOTS if directory else name in DATABASES) or (
        len(p.parts) > 1 and p.parts[0] in ROOTS)
    _check(valid, 'unsupported image path')
    return name


def _implementation():
    # Release version alone is insufficient while this alpha changes under the
    # same package version. Compare installed/source Python code, not its path.
    result = {}
    for package in ('pulse_system', 'pulse_harness', 'stream_cognition', 'per_pulse'):
        spec = importlib.util.find_spec(package)
        if spec is None or not spec.submodule_search_locations:
            continue
        root = Path(next(iter(spec.submodule_search_locations)))
        digest = hashlib.sha256()
        for p in sorted(root.rglob('*.py')):
            digest.update(p.relative_to(root).as_posix().encode() + b'\0' + p.read_bytes() + b'\0')
        result[package] = digest.hexdigest()
    return result


def _inventory(home):
    try:
        return _scan_inventory(home)
    except (FileNotFoundError, NotADirectoryError):
        # A live checkpoint writer can rename a temporary file between
        # scandir and stat, before the journal/lease checks are reached.
        # Refuse the changing body without exposing its private path.
        raise ImageError('stop the source cleanly first; source files changed during inspection') from None


def _scan_inventory(home):
    files, directories = {}, []
    for name in DATABASES:
        p = home/name
        if p.exists() or p.is_symlink():
            files[name] = _stamp(p)
    _check(all(x in files for x in DATABASES[:2]), 'normal product world.db and kernel.db are required')
    def descend(relative):
        p = home/relative
        _check(stat.S_ISDIR(p.lstat().st_mode), 'resource roots and directories cannot be links')
        directories.append(relative)
        _check(len(files)+len(directories) <= MAX_ENTRIES, 'image entry limit exceeded')
        with os.scandir(p) as entries:
            for entry in entries:
                name = relative + '/' + entry.name
                _path(name, directory=entry.is_dir(follow_symlinks=False))
                if entry.is_dir(follow_symlinks=False):
                    descend(name)
                else:
                    files[name] = _stamp(home/name)
                _check(len(files)+len(directories) <= MAX_ENTRIES, 'image entry limit exceeded')
    for root in ROOTS:
        p = home/root
        if p.exists() or p.is_symlink():
            descend(root)
    _check('workspace' in directories, 'normal product workspace is required')
    _check(sum(s[2] for s in files.values()) <= MAX_TOTAL, 'image size limit exceeded')
    return files, sorted(directories)


def _stopped(db, *, include_recovered_history=False):
    lease = db.execute("SELECT state FROM runtime_leases WHERE scope='pulse_world'").fetchone()
    _check(lease is not None and lease[0] == 'released',
           'stop the source cleanly first; an active or expired lease cannot be exported')
    states = "('running')" if include_recovered_history else "('running','uncertain')"
    _check(db.execute(f"SELECT count(*) FROM harness_turns WHERE state IN {states}").fetchone()[0] == 0,
           'source still has unsettled native turns; recover and stop before saving')


def _recovered_history(world, kernel, world_id):
    """Verify retained recovery evidence without changing either native store.

    A recovered or timed-out World record remains uncertain after its Kernel
    turn finishes. It is history, not permission to replay a request or an
    effect. Native timeouts additionally require accepted input and the exact
    cancelled Kernel turn; unfinished/uncertain effects are rejected separately.
    """
    rows = world.execute(
        "SELECT t.id, t.engram_id, t.execution_context, t.error_code, "
        "t.error_phase, t.settled_at, e.world_id, e.engram_id, e.status, e.settled_at, t.prompt_accepted "
        "FROM harness_turns t LEFT JOIN causal_events e ON e.id = t.event_id "
        "WHERE t.state = 'uncertain' ORDER BY t.id"
    ).fetchall()
    _check(len(rows) <= MAX_ENTRIES, 'too many recovered turns for an image manifest')
    for turn_id, engram_id, context, code, phase, settled, event_world, event_engram, status, event_settled, accepted in rows:
        recovered = code == 'process_recovered' and phase == 'recovery'
        timed_out = code == 'world_turn_timeout' and phase == 'kernel' and accepted == 1
        _check(isinstance(turn_id, str) and 0 < len(turn_id) <= 256
               and (recovered or timed_out) and bool(settled)
               and event_world == world_id and event_engram == engram_id
               and status == 'uncertain' and bool(event_settled),
               'uncertain World history is not a settled process recovery or native timeout')
        matches = kernel.execute(
            "SELECT world_id, engram_id, context_id, status FROM turns WHERE turn_id = ?",
            (turn_id,),
        ).fetchall()
        expected_context = '' if context == 'home' else context
        _check(len(matches) == 1 and matches[0][:3] == (world_id, engram_id, expected_context)
               and matches[0][3] in (('cancelled',) if timed_out else ('succeeded', 'failed', 'cancelled')),
               'timed-out World history has no matching cancelled Kernel turn' if timed_out else
               'recovered World history has no matching finished Kernel turn')
    return [row[0] for row in rows]


def _identity(world, kernel):
    row = world.execute("SELECT state FROM component_state WHERE component='pulse.world.v1'").fetchone()
    _check(row is not None, 'missing persistent world identity')
    value = json.loads(row[0]); world_id = value.get('world_id')
    _check(isinstance(world_id,str) and bool(world_id), 'invalid persistent world identity')
    _check(kernel.execute('SELECT count(*) FROM sessions WHERE world_id != ?', (world_id,)).fetchone()[0] == 0,
           'world and Kernel do not belong to one product body')
    return world_id


def _no_pending(db):
    _check(db.execute("SELECT count(*) FROM turns WHERE status NOT IN ('succeeded','failed','cancelled')").fetchone()[0] == 0,
           'Kernel has unfinished turns; recover and stop before saving')
    _check(db.execute("SELECT count(*) FROM actions WHERE status NOT IN ('succeeded','failed','cancelled')").fetchone()[0] == 0,
           'Kernel has unfinished actions; recover and stop before saving')
    _check(db.execute("SELECT count(*) FROM operations WHERE status NOT IN ('succeeded','failed','cancelled')").fetchone()[0] == 0,
           'Kernel has unfinished operations; recover and stop before saving')


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def save(home: Path, archive: Path, *, include_recovered_history: bool = False) -> dict:
    _check(os.name == 'posix', 'cold Image currently supports POSIX hosts')
    home, archive = home.expanduser().resolve(), archive.expanduser().absolute()
    _check(not archive.resolve().is_relative_to(home), 'save the image outside its source home')
    _check(not (home/INCOMPLETE).exists(), 'source image restore is incomplete')
    _check(not archive.exists() and not archive.is_symlink(), 'image output already exists')
    files, directories = _inventory(home)
    deadline = time.monotonic()+60
    with tempfile.TemporaryDirectory(prefix='pulse-image-') as tmp, ExitStack() as stack:
        stage = Path(tmp)
        # Acquire all writer locks before any read. Proper Runtime startup needs
        # this World lock; optional standalone journal writers cannot cross the
        # multi-database snapshot either. Locks are rolled back, not committed.
        locks = {}
        for name in DATABASES:
            if name not in files:
                continue
            db = stack.enter_context(closing(sqlite3.connect((home/name).as_uri()+'?mode=rw', uri=True,
                                                            timeout=0, isolation_level=None)))
            db.execute('BEGIN IMMEDIATE')
            locks[name] = db
        _stopped(locks['world.db'], include_recovered_history=include_recovered_history)
        _no_pending(locks['kernel.db'])
        world_id = _identity(locks['world.db'],locks['kernel.db'])
        recovered = _recovered_history(locks['world.db'], locks['kernel.db'], world_id) if include_recovered_history else []
        implementation = _implementation()
        if 'kernel.db.cognition.sqlite3' in files:
            _check('stream_cognition' in implementation, 'install the source cognition module before saving its journal')
        else:
            implementation.pop('stream_cognition', None)
        entries = []
        def progress(*_):
            _check(time.monotonic() < deadline, 'snapshot exceeded its time allowance')
        for name, expected in sorted(files.items()):
            progress(); dest = stage/name; dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if name in locks:
                # A different read connection can use backup while the writer
                # reservation prevents mutation; backup on the write connection
                # itself would wait on its own transaction.
                with closing(sqlite3.connect((home/name).as_uri()+'?mode=ro', uri=True)) as source:
                    with closing(sqlite3.connect(dest)) as target:
                        source.backup(target, pages=256, progress=progress, sleep=.01)
            else:
                fd = os.open(home/name, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                with os.fdopen(fd,'rb') as source, dest.open('xb') as target:
                    _check(_stamp(home/name) == expected, 'source changed during snapshot')
                    copied = 0
                    for chunk in iter(lambda:source.read(1024*1024), b''):
                        progress(); copied += len(chunk)
                        _check(copied <= MAX_FILE, 'resource grew beyond the image limit')
                        target.write(chunk)
                _check(_stamp(home/name) == expected, 'source changed during snapshot')
            mode = 0o700 if expected[4] & stat.S_IXUSR and name not in DATABASES else 0o600
            dest.chmod(mode)
            _check(dest.stat().st_size <= MAX_FILE, 'snapshot database limit exceeded')
            entries.append({'path':name, 'bytes':dest.stat().st_size, 'sha256':_hash(dest), 'mode':mode})
        after, dirs_after = _inventory(home)
        _check(after == files and dirs_after == directories, 'source changed during snapshot')
        _check(sum(x['bytes'] for x in entries) <= MAX_TOTAL, 'snapshot size limit exceeded')
        manifest = {'format':FORMAT, 'created_at':datetime.now(timezone.utc).isoformat(),
                    'implementation':implementation, 'world_id':world_id, 'directories':directories, 'files':entries,
                    'omitted_root_entries':sorted(p.name for p in home.iterdir() if p.name not in ROOTS and p.name not in DATABASES),
                    'privacy':'private history and material, NOT a sanitized public distribution',
                    'rebind':['model setup and credentials','external resources and command image',
                              'optional module activation','workspace-root-bound historical operations'],
                    'semantics':'restore_copy; source is not retired; no startup or model calls'}
        if recovered:
            manifest['retained_uncertain_turns'] = recovered
        manifest_bytes = json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')
        _check(len(manifest_bytes) <= MAX_MANIFEST, 'image manifest limit exceeded')
        # Create-only publication: a collision never replaces an existing file.
        archive.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.pulse-image-', dir=archive.parent); os.close(fd)
        try:
            with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as z:
                z.writestr('manifest.json',manifest_bytes)
                for entry in entries:
                    z.write(stage/entry['path'], entry['path'])
            with open(temporary,'rb') as f: os.fsync(f.fileno())
            os.link(temporary,archive)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return {'saved':str(archive), 'files':len(entries), 'sha256':_hash(archive),
            'model_calls':0, 'privacy':manifest['privacy'], 'rebind':manifest['rebind'],
            'retained_uncertain_turns':recovered}


def _manifest(z):
    infos = z.infolist(); names = [x.filename for x in infos]
    _check(len(infos) <= MAX_ENTRIES+1 and len(set(names)) == len(names), 'duplicate or excessive archive entries')
    _check('manifest.json' in names and z.getinfo('manifest.json').file_size <= MAX_MANIFEST, 'invalid image manifest')
    manifest = json.loads(z.read('manifest.json'))
    _check(isinstance(manifest,dict) and manifest.get('format') == FORMAT, 'unsupported image format')
    recovered = manifest.get('retained_uncertain_turns', [])
    _check(isinstance(recovered, list) and len(recovered) <= MAX_ENTRIES
           and all(isinstance(item, str) and 0 < len(item) <= 256 for item in recovered)
           and recovered == sorted(set(recovered)), 'invalid retained recovery history')
    _check(isinstance(manifest.get('world_id'),str) and bool(manifest['world_id'])
           and manifest.get('semantics') == 'restore_copy; source is not retired; no startup or model calls'
           and isinstance(manifest.get('rebind'),list)
           and all(isinstance(item,str) for item in manifest['rebind']), 'invalid image identity or transfer semantics')
    files, dirs = manifest.get('files'), manifest.get('directories')
    _check(isinstance(files,list) and isinstance(dirs,list) and len(files)+len(dirs) <= MAX_ENTRIES,
           'invalid image inventory')
    expected = set()
    for entry in files:
        _check(isinstance(entry,dict) and set(entry)=={'path','bytes','sha256','mode'}, 'invalid file metadata')
        name = _path(entry['path']); _check(name not in expected, 'duplicate image file')
        expected.add(name)
        _check(type(entry['bytes']) is int and 0 <= entry['bytes'] <= MAX_FILE and entry['mode'] in (0o600,0o700),
               'invalid image file bounds')
        _check(isinstance(entry['sha256'],str) and len(entry['sha256'])==64, 'invalid file digest')
        _check(name in names, 'image is missing a file')
        info = z.getinfo(name)
        mode = info.external_attr >> 16
        _check(not info.is_dir() and not stat.S_ISLNK(mode) and not (info.flag_bits & 1)
               and info.file_size == entry['bytes'], 'invalid archive member')
    for name in dirs: _path(name,directory=True)
    _check(len(set(dirs)) == len(dirs) and not expected.intersection(dirs), 'invalid directory inventory')
    _check(set(names)==expected|{'manifest.json'} and set(DATABASES[:2]) <= expected and 'workspace' in dirs,
           'archive does not contain the supported complete layout')
    _check(sum(x['bytes'] for x in files) <= MAX_TOTAL, 'image size limit exceeded')
    for name in expected|set(dirs):
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.': _check(str(parent) in dirs, 'missing or conflicting directory')
    impl = manifest.get('implementation')
    _check(isinstance(impl,dict) and {'pulse_system','pulse_harness'} <= set(impl)
           and set(impl) <= {'pulse_system','pulse_harness','stream_cognition','per_pulse'}, 'invalid implementation binding')
    if 'kernel.db.cognition.sqlite3' in expected:
        _check('stream_cognition' in impl, 'missing cognition implementation binding')
    return manifest


def _verify(z, manifest, destination=None):
    for entry in manifest['files']:
        digest = hashlib.sha256()
        with z.open(entry['path']) as source, ExitStack() as stack:
            target = None
            if destination is not None:
                target = stack.enter_context((destination/entry['path']).open('xb'))
                os.fchmod(target.fileno(),entry['mode'])
            count = 0
            for chunk in iter(lambda:source.read(1024*1024), b''):
                count += len(chunk); _check(count <= entry['bytes'], 'archive content exceeds declared size')
                digest.update(chunk)
                if target is not None: target.write(chunk)
            if target is not None:
                target.flush(); os.fsync(target.fileno())
        _check(count==entry['bytes'] and digest.hexdigest()==entry['sha256'], 'image file checksum mismatch')


def inspect(archive: Path) -> dict:
    with zipfile.ZipFile(archive.expanduser()) as z:
        manifest = _manifest(z); _verify(z,manifest)
    current = _implementation()
    return {**manifest, 'integrity':'verified_hashes_not_authenticity', 'model_calls':0,
            'compatible':all(current.get(k)==v for k,v in manifest['implementation'].items())}


def restore(archive: Path, home: Path) -> dict:
    # A partial destination never becomes an implicitly runnable product home.
    # We keep its marker on failure rather than deleting potentially useful data.
    _check(os.name == 'posix', 'cold Image currently supports POSIX hosts')
    home = home.expanduser().absolute()
    archive = archive.expanduser().absolute()
    _check(not home.exists() and not home.is_symlink(), 'restore requires a new destination home')
    with zipfile.ZipFile(archive) as z:
        manifest = _manifest(z); _verify(z,manifest)
        current = _implementation()
        _check(all(current.get(k)==v for k,v in manifest['implementation'].items()),
               'restore requires the same implementation; use the original release or an explicit migration')
        home.mkdir(parents=True,mode=0o700,exist_ok=False)
        marker = home/INCOMPLETE; marker.write_text('incomplete cold-image restore; do not start\n'); marker.chmod(0o600)
        for name in sorted(manifest['directories'],key=lambda x:(x.count('/'),x)):
            (home/name).mkdir(mode=0o700)
        _verify(z,manifest,home)
        with closing(sqlite3.connect((home/'world.db').resolve().as_uri()+'?mode=ro',uri=True)) as w:
            with closing(sqlite3.connect((home/'kernel.db').resolve().as_uri()+'?mode=ro',uri=True)) as k:
                recovered = manifest.get('retained_uncertain_turns', [])
                _stopped(w, include_recovered_history=bool(recovered)); _no_pending(k)
                world_id = _identity(w,k)
                _check(world_id==manifest.get('world_id'), 'restored identity differs from its manifest')
                _check(_recovered_history(w,k,world_id)==recovered,
                       'restored recovery history differs from its manifest')
        receipt = {'format':FORMAT, 'source_sha256':_hash(archive),
                   'restored_at':datetime.now(timezone.utc).isoformat(), 'semantics':manifest['semantics'],
                   'rebind':manifest['rebind'], 'world_id':manifest['world_id'], 'model_calls':0,
                   'retained_uncertain_turns':recovered}
        p = home/'image-origin.json'; p.write_text(json.dumps(receipt,indent=2)); p.chmod(0o600)
        marker.unlink()
    return {'restored':str(home), 'files':len(manifest['files']), **receipt,
            'next':'run explicit model setup in this new home, then start with chosen resources'}


def add_commands(subs):
    image = subs.add_parser('image',help='save/inspect/restore a private stopped Context Body; no model calls')
    commands = image.add_subparsers(dest='image_command',required=True)
    for name in ('save','inspect','restore'):
        p = commands.add_parser(name); p.add_argument('archive',type=Path)


def execute(args, home):
    try:
        operation = {'save':lambda:save(home,args.archive),'inspect':lambda:inspect(args.archive),
                     'restore':lambda:restore(args.archive,home)}[args.image_command]
        print(json.dumps(operation(),ensure_ascii=False,indent=2)); return 0
    except ImageError:
        raise
    except (OSError,sqlite3.Error,zipfile.BadZipFile,ValueError,KeyError,TypeError,RecursionError):
        raise ImageError('image operation failed; preserve any incomplete destination and inspect source/layout') from None
