"""Release gate: warm API, real label inference, and configured shelf readiness."""
import io
import json
from pathlib import Path
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import uuid

BASE = 'http://127.0.0.1:8000/v1'
DEFAULT_SMOKE_SLUG = 'chateau-tamagne-nature-vert'


def get(path):
    with urlopen(BASE + path, timeout=15) as response:
        return json.load(response)


def validate_scan(body, expected_slug):
    if not isinstance(body, dict) or not isinstance(body.get('not_in_catalog'), bool):
        raise RuntimeError('Label smoke: expected a rich recognition response, not empty flat success')
    if expected_slug is not None:
        if body.get('slug') != expected_slug or body['not_in_catalog']:
            raise RuntimeError('Label smoke: known reference was not recognized correctly')
    elif body.get('slug') or not body['not_in_catalog']:
        raise RuntimeError('Label smoke: negative image produced a wine card')


def scan(data, filename):
    boundary = uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    request = Request(BASE + '/scan/photo?flat=0', data=body,
                      headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def check_labels(env):
    from PIL import Image

    slug = env.get('RELEASE_SCAN_SMOKE_SLUG') or DEFAULT_SMOKE_SLUG
    image = Path(env.get('RELEASE_SCAN_SMOKE_IMAGE') or
                 str(Path(env.get('CASE_DATA_DIR', '/opt/somelye/data/case')) / 'thumbs' / (slug + '.webp')))
    validate_scan(scan(image.read_bytes(), image.name), slug)
    buffer = io.BytesIO()
    Image.new('RGB', (512, 512), 'white').save(buffer, 'PNG')
    validate_scan(scan(buffer.getvalue(), 'negative.png'), None)


def main():
    env = {}
    for line in Path('/opt/somelye/somelye.env').read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    assert get('/healthz')['warm'] is True, 'API has not warmed up'
    paths = get('/openapi.json')['paths']
    for path in ('/v1/scan/photo', '/v1/sommelier/shelf-selection', '/v1/shelf/jobs', '/v1/shelf/health'):
        assert path in paths, f'Missing endpoint {path}'
    if env.get('IMAGE_PROVIDER') in ('real', 'winescan'):
        check_labels(env)
    if env.get('VINCHIK_SHELF_URL'):
        deadline = time.monotonic() + 180
        while True:
            try:
                health = get('/shelf/health')
                if health['ready'] is True and health['catalogSize'] > 0:
                    break
            except (URLError, TimeoutError):
                pass
            assert time.monotonic() < deadline, 'Shelf is not ready after 180s'
            time.sleep(3)
    print('API, label recognition and configured shelf release gates passed')


if __name__ == '__main__':
    main()
