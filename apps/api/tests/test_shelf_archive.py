import json
import os
import time

from app import shelf_archive as archive
from app.routers import shelf_gateway as gateway


def test_disabled(monkeypatch):
    monkeypatch.delenv('VINCHIK_SHELF_ARCHIVE_DIR', raising=False)
    assert archive.save_input('a'*32, b'photo') is None


def test_archive_preserves_bytes_result_and_private_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv('VINCHIK_SHELF_ARCHIVE_DIR', str(tmp_path/'archive'))
    content=b'\x89PNG\r\n\x1a\noriginal'
    result={'matches':[{'wineId':'white-dry'}], 'warnings':[]}
    monkeypatch.setattr(gateway, 'remote_json', lambda *args:(200,result))
    jobs=gateway.Jobs(); key='a'*32; jobs.jobs[key]=gateway.Job('private-owner')
    jobs.run(key,content)
    root=tmp_path/'archive'; folder=root/key
    assert (folder/'image.png').read_bytes() == content
    assert json.loads((folder/'result.json').read_text())['result'] == result
    assert json.loads((folder/'input.json').read_text())['bytes'] == len(content)
    assert root.stat().st_mode & 0o777 == 0o700
    assert folder.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in folder.iterdir())
    assert 'private-owner' not in ''.join(p.read_text() for p in folder.glob('*.json'))


def test_failure_is_saved_and_bad_archive_does_not_break_scan(tmp_path, monkeypatch):
    monkeypatch.setenv('VINCHIK_SHELF_ARCHIVE_DIR', str(tmp_path))
    monkeypatch.setattr(gateway, 'remote_json', lambda *args:(503,{'detail':'unavailable'}))
    jobs=gateway.Jobs(); key='b'*32; jobs.jobs[key]=gateway.Job('owner'); jobs.run(key,b'bad-image')
    assert json.loads((tmp_path/key/'result.json').read_text())['state'] == 'failed'
    assert (tmp_path/key/'image.bin').read_bytes() == b'bad-image'
    monkeypatch.setenv('VINCHIK_SHELF_ARCHIVE_DIR', str(tmp_path/key/'image.bin'))
    key='c'*32; jobs.jobs[key]=gateway.Job('owner'); jobs.run(key,b'photo')
    assert jobs.read(key,'owner')['state'] == 'failed'


def test_retention_only_removes_archive_directories(tmp_path, monkeypatch):
    monkeypatch.setenv('VINCHIK_SHELF_ARCHIVE_DIR', str(tmp_path))
    monkeypatch.setattr(archive,'MAX_SCANS',2)
    keep=tmp_path/'notes';keep.mkdir(); (keep/'note').write_text('keep')
    first=archive.save_input('a'*32,b'first')
    os.utime(first,(time.time()-archive.RETENTION_SECONDS-1,)*2)
    archive.save_input('b'*32,b'second')
    assert not first.exists()
    archive.save_input('c'*32,b'third'); archive.save_input('d'*32,b'fourth')
    assert (keep/'note').read_text() == 'keep'
    assert len([p for p in tmp_path.iterdir() if len(p.name)==32]) == 2
