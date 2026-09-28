"""Release gate: deployed API contract, warmup, real shelf health when configured."""
import json
import time
from urllib.error import URLError
from pathlib import Path
from urllib.request import urlopen

base = 'http://127.0.0.1:8000/v1'
def get(path):
    with urlopen(base + path, timeout=15) as response:
        return json.load(response)

assert get('/healthz')['warm'] is True, 'API has not warmed up'
paths = get('/openapi.json')['paths']
for path in ('/v1/sommelier/shelf-selection', '/v1/shelf/jobs', '/v1/shelf/health'):
    assert path in paths, f'Missing endpoint {path}'
configured = any(line.startswith('VINCHIK_SHELF_URL=') and line.split('=', 1)[1].strip()
                 for line in Path('/opt/somelye/somelye.env').read_text().splitlines())
if configured:
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
print('API and configured shelf release gates passed')
