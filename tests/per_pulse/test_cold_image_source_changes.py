"""Refuse a changing source without publishing a partial private archive.

The barriers select an actual filesystem interleaving; no model is called.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import threading

import pytest

from pulse_system.product import image
from test_cli_cold_image import _seed_stopped_home

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='cold Image requires POSIX')


@pytest.mark.parametrize('inventory_pass', [1, 2])
@pytest.mark.parametrize('change', ['remove_file', 'replace_parent'])
def test_source_disappearing_during_inventory_refuses_and_releases_reservations(
    tmp_path, monkeypatch, inventory_pass, change,
):
    home, archive = tmp_path / 'source', tmp_path / 'private.zip'
    _seed_stopped_home(home)
    directory = home / 'workspace' / 'changing-directory'
    directory.mkdir()
    victim = directory / 'private-material-name.txt'
    victim.write_text('Original private material.\n')
    entered, release = threading.Event(), threading.Event()
    original_inventory, original_stamp = image._inventory, image._stamp
    passes = 0

    def observe_inventory(path):
        nonlocal passes
        passes += 1
        return original_inventory(path)

    def pause_before_stat(path):
        if path == victim and passes == inventory_pass:
            entered.set()
            assert release.wait(5), 'Release the actual filesystem read boundary.'
        return original_stamp(path)

    monkeypatch.setattr(image, '_inventory', observe_inventory)
    monkeypatch.setattr(image, '_stamp', pause_before_stat)
    with ThreadPoolExecutor(max_workers=1) as pool:
        saving = pool.submit(image.save, home, archive, include_recovered_history=True)
        try:
            assert entered.wait(5)
            victim.unlink()
            if change == 'replace_parent':
                directory.rmdir()
                directory.write_text('The directory became a regular file.\n')
        finally:
            release.set()
        with pytest.raises(image.ImageError, match='stop the source cleanly.*source.*changed') as refused:
            saving.result(timeout=5)
    assert victim.name not in str(refused.value)
    assert not archive.exists() and not list(tmp_path.glob('.pulse-image-*'))
    # The final-inventory path held all native journal reservations. A later
    # ordinary save must work after this failed attempt unwinds them.
    monkeypatch.setattr(image, '_inventory', original_inventory)
    monkeypatch.setattr(image, '_stamp', original_stamp)
    if directory.is_file():
        directory.unlink()
        directory.mkdir()
    victim.write_text('Original private material.\n')
    assert image.save(home, archive)['model_calls'] == 0
    assert image.inspect(archive)['integrity'] == 'verified_hashes_not_authenticity'
