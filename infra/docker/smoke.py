"""Check Docker's public ingress, auth and optionally a real shelf photo. No dependencies."""
import argparse
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--shelf', action='store_true')
    parser.add_argument('--photo', type=Path)
    args = parser.parse_args()
    base = args.url.rstrip('/')

    def request(path, data=None, headers=None):
        with urlopen(Request(base + path, data=data, headers=headers or {}), timeout=30) as response:
            return json.load(response)

    assert request('/v1/healthz')['warm'] is True
    with urlopen(base + '/shelf', timeout=10) as response:
        assert 'text/html' in response.headers['Content-Type']
        assert b'<html' in response.read().lower()
    token = request('/v1/auth/guest', json.dumps({
        'age_confirmed': True, 'consent_version': 'docker-smoke',
    }).encode(), {'Content-Type': 'application/json'})['access_token']
    headers = {'Authorization': 'Bearer ' + token}
    request('/v1/catalog?limit=1', headers=headers)
    request('/v1/sommelier/shelf-selection', json.dumps({'wish': 'белое сухое'}).encode(),
            {**headers, 'Content-Type': 'application/json'})
    print('Web, API readiness, guest auth, catalog and sommelier ranking: OK', flush=True)
    if args.shelf or args.photo:
        health = request('/v1/shelf/health')
        assert health['ready'] is True and health['catalogSize'] > 0
        print('Shelf readiness:', health['catalogSize'], 'catalog entries')
    if args.photo:
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
                'filename="shelf.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode()
        body += args.photo.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
        try:
            request('/v1/shelf/jobs', body, {'Content-Type': 'multipart/form-data; boundary=' + boundary})
        except HTTPError as exc:
            assert exc.code == 401, exc.code
        else:
            raise AssertionError('Unauthenticated upload was accepted')
        job = request('/v1/shelf/jobs', body, {
            **headers, 'Content-Type': 'multipart/form-data; boundary=' + boundary,
        })
        deadline = time.monotonic() + 330
        while time.monotonic() < deadline:
            result = request('/v1/shelf/jobs/' + job['jobId'], headers=headers)
            if result['state'] != 'running':
                assert result['state'] == 'done', result
                assert result['status'] == 200, result
                scan = result['result']
                print(json.dumps({'detected': scan['detectedCount'], 'matches': scan['matches'],
                                  'timingsMs': scan['timingsMs']}, ensure_ascii=False))
                found = sorted({match['wineId'] for match in scan['matches']})
                selection = request('/v1/sommelier/shelf-selection', json.dumps({
                    'wish': 'белое сухое', 'wine_ids': found,
                }).encode(), {**headers, 'Content-Type': 'application/json'})
                assert all(wine['wine_id'] in found for wine in selection['wines'])
                print('Ranked white dry matches:', len(selection['wines']),
                      'eligible catalog:', selection['total_eligible'])
                break
            time.sleep(2)
        else:
            raise TimeoutError('Shelf job exceeded 330s')


if __name__ == '__main__':
    main()
