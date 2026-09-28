import threading
import time

import pytest

from app.routers import shelf_gateway as gateway
from tests.conftest import auth_header, make_guest


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv('VINCHIK_SHELF_URL', 'http://127.0.0.1:18086')


def test_disabled_health_is_not_spa(client, monkeypatch):
    monkeypatch.delenv('VINCHIK_SHELF_URL', raising=False)
    r = client.get('/v1/shelf/health')
    assert r.status_code == 503 and r.json()['ready'] is False


def test_health_does_not_leak_upstream(client, enabled, monkeypatch):
    monkeypatch.setattr(gateway, 'remote_json', lambda *a: (200, {'ready': True, 'catalogSize': 2103, 'secret': 'hidden'}))
    r = client.get('/v1/shelf/health')
    assert r.status_code == 200 and r.json()['asyncJobs'] is True
    assert 'secret' not in r.json()


def test_job_requires_authentication(client, enabled):
    assert client.post('/v1/shelf/jobs', files={'image': ('a.jpg', b'abc')}).status_code == 401
    assert client.get('/v1/shelf/jobs/missing').status_code == 401


def test_job_returns_before_model_finishes_and_is_private(client, enabled, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    def slow(*args):
        started.set()
        finish.wait(5)
        return 200, {'matches': [], 'image': {'width': 10, 'height': 10}}
    monkeypatch.setattr(gateway, 'remote_json', slow)
    owner, other = auth_header(make_guest(client)), auth_header(make_guest(client))
    try:
        r = client.post('/v1/shelf/jobs', headers=owner, files={'image': ('a.jpg', b'abc')})
        assert r.status_code == 202 and started.wait(1)
        url = '/v1/shelf/jobs/' + r.json()['jobId']
        assert client.get(url, headers=owner).json()['state'] == 'running'
        assert client.get(url, headers=other).status_code == 404
        busy = client.post('/v1/shelf/jobs', headers=other, files={'image': ('a.jpg', b'abc')})
        assert busy.status_code == 503 and busy.headers['retry-after'] == '3'
    finally:
        finish.set()
    for _ in range(100):
        result = client.get(url, headers=owner).json()
        if result['state'] != 'running':
            break
        time.sleep(.01)
    assert result['state'] == 'done' and result['result']['matches'] == []


def test_job_failure_is_reported_and_releases_slot(client, enabled, monkeypatch):
    monkeypatch.setattr(gateway, 'remote_json', lambda *args: (503, {'detail': 'unavailable'}))
    store = gateway.Jobs()
    key = store.start('owner', b'abc')
    for _ in range(100):
        result = store.read(key, 'owner')
        if result['state'] != 'running':
            break
        time.sleep(.01)
    assert result['state'] == 'failed' and result['status'] == 503
    assert store.start('owner', b'abc') != key


def test_expired_results_are_removed():
    store = gateway.Jobs()
    store.jobs['old'] = gateway.Job('owner', 'done', 200, {}, time.monotonic() - 121)
    with pytest.raises(gateway.HTTPException) as e:
        store.read('old', 'owner')
    assert e.value.status_code == 404 and not store.jobs


def test_empty_and_large_images_rejected(client, enabled, monkeypatch):
    monkeypatch.setattr(gateway, 'MAX_BYTES', 3)
    h = auth_header(make_guest(client))
    assert client.post('/v1/shelf/jobs', headers=h, files={'image': ('a.jpg', b'')}).status_code == 422
    assert client.post('/v1/shelf/jobs', headers=h, files={'image': ('a.jpg', b'1234')}).status_code == 413


def test_sync_scan_uses_same_service(client, enabled, monkeypatch):
    expected = {'matches': [], 'image': {'width': 1, 'height': 1}}
    monkeypatch.setattr(gateway, 'remote_json', lambda *args: (200, expected))
    r = client.post('/v1/shelf/scan', headers=auth_header(make_guest(client)), files={'image': ('a.jpg', b'x')})
    assert r.status_code == 200 and r.json() == expected
