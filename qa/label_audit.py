"""Sequential, read-only model audit against an existing API; images/results stay outside Git."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    point = (len(values) - 1) * fraction
    lo = int(point)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (point - lo)


def summarize(rows):
    scored = [r for r in rows if r['truth_kind'] in ('known', 'absent')]
    positives = [r for r in scored if r['truth_kind'] == 'known']
    negatives = [r for r in scored if r['truth_kind'] == 'absent']

    def success(row):
        return row.get('http_status') == 200 and not row.get('error')

    def accepted(row):
        response = row.get('response') or {}
        if not isinstance(response, dict):
            return False
        return success(row) and bool(response.get('slug')) and response.get('not_in_catalog') is False

    def candidates(row):
        if not success(row):
            return []
        return [m['slug'] for m in row.get('response', {}).get('matches', []) if isinstance(m, dict) and 'slug' in m]

    confident = [r for r in scored if accepted(r)]
    correct = [r for r in positives if accepted(r) and r['response']['slug'] == r['true_slug']]
    rejected = [r for r in negatives if success(r) and r['response'].get('not_in_catalog') is True]
    latency = [r['elapsed_ms'] for r in rows if success(r)]
    return {
        'n': len(rows), 'known': len(positives), 'absent': len(negatives),
        'unscored': len(rows) - len(scored),
        'http_or_protocol_errors': sum(not success(r) for r in rows),
        'candidate_top1_correct': sum(bool(candidates(r)) and candidates(r)[0] == r['true_slug'] for r in positives),
        'candidate_top5_correct': sum(r['true_slug'] in candidates(r)[:5] for r in positives),
        'correct_accepted': len(correct), 'accepted_total': len(confident),
        'wrong_accepted_known': sum(accepted(r) and r['response']['slug'] != r['true_slug'] for r in positives),
        'false_accepts_absent': sum(accepted(r) for r in negatives),
        'correct_rejections': len(rejected),
        'precision_accepted': len(correct) / len(confident) if confident else None,
        'recall_accepted_known': len(correct) / len(positives) if positives else None,
        'end_to_end_accuracy': (len(correct) + len(rejected)) / len(scored) if scored else None,
        'latency_ms': {'p50': percentile(latency, .5), 'p95': percentile(latency, .95),
                       'max': max(latency) if latency else None, 'n': len(latency)},
        'over_3s': sum(r['elapsed_ms'] > 3000 for r in rows),
        'over_10s': sum(r['elapsed_ms'] > 10000 for r in rows),
    }


def run(args):
    manifest = json.loads(args.manifest.read_text())
    if args.out.exists():
        raise SystemExit('Output already exists; choose a new path to avoid mixing configurations')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    route = '/v1/scan/photo?flat=0' if args.mode == 'rich' else '/v1/eval/predict'
    metadata = {'base_url': args.api, 'route': route,
                'started_at': datetime.now(timezone.utc).isoformat(),
                'manifest_sha256': hashlib.sha256(args.manifest.read_bytes()).hexdigest()}
    args.out.with_suffix('.metadata.json').write_text(json.dumps(metadata, indent=2))
    rows = []
    with args.out.open('x') as output:
        for item in manifest:
            path = Path(item['path'])
            if not path.is_absolute() and args.images_root is not None:
                path = args.images_root / path
            data = path.read_bytes()
            assert hashlib.sha256(data).hexdigest() == item['sha256'], path
            boundary = uuid.uuid4().hex
            mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="audit{path.suffix}"\r\n'
                    f'Content-Type: {mime}\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
            row = {k: v for k, v in item.items() if k != 'path'}
            started = time.monotonic()
            try:
                request = Request(args.api.rstrip('/') + route, data=body,
                                  headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
                with urlopen(request, timeout=args.timeout) as response:
                    row['http_status'] = response.status
                    row['response'] = json.load(response)
                    if not isinstance(row['response'], dict):
                        raise ValueError('Expected JSON object')
                    if args.mode == 'rich' and not isinstance(row['response'].get('not_in_catalog'), bool):
                        raise ValueError('Missing recognition gate in rich response')
            except HTTPError as exc:
                row['http_status'] = exc.code
                row['error'] = 'HTTP ' + str(exc.code)
            except Exception as exc:
                row['error'] = type(exc).__name__ + ': ' + str(exc)
            row['elapsed_ms'] = round((time.monotonic() - started) * 1000, 2)
            rows.append(row)
            output.write(json.dumps(row, ensure_ascii=False) + '\n')
            output.flush()
            if len(rows) % 10 == 0:
                print(len(rows), '/', len(manifest), 'errors', sum(bool(r.get('error')) for r in rows), flush=True)
    if args.mode == 'rich':
        metrics = summarize(rows)
        args.out.with_suffix('.metrics.json').write_text(json.dumps(metrics, indent=2))
        print(json.dumps(metrics, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--api', required=True)
    parser.add_argument('--images-root', type=Path, help='Root for relative manifest paths')
    parser.add_argument('--mode', choices=['rich', 'flat'], default='rich')
    parser.add_argument('--timeout', type=float, default=45)
    run(parser.parse_args())
