"""Opt-in, private, bounded archive for reproducing shelf recognition failures."""
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import time

log = logging.getLogger(__name__)
MAX_SCANS = 100
RETENTION_SECONDS = 7 * 86400
_KEY = re.compile(r"[a-f0-9]{32}")


def _write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False)
    temporary.replace(path)


def save_input(key: str, image: bytes) -> Path | None:
    directory = os.environ.get('VINCHIK_SHELF_ARCHIVE_DIR')
    if not directory:
        return None
    try:
        if not _KEY.fullmatch(key):
            raise ValueError('Invalid archive key')
        root = Path(directory)
        if not root.is_absolute():
            raise ValueError('Archive directory must be absolute')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        # Only our UUID directories are eligible for cleanup; preserve other files.
        existing = sorted((p for p in root.iterdir() if _KEY.fullmatch(p.name)
                           and p.is_dir() and not p.is_symlink()), key=lambda p:p.stat().st_mtime)
        now = time.time()
        for i, entry in enumerate(existing):
            if now-entry.stat().st_mtime > RETENTION_SECONDS or i <= len(existing)-MAX_SCANS:
                shutil.rmtree(entry)
        target = root / key
        target.mkdir(mode=0o700)
        extension = '.png' if image.startswith(b'\x89PNG\r\n\x1a\n') else '.jpg' if image.startswith(b'\xff\xd8') else '.bin'
        name = 'image' + extension
        fd = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(image)
        _write_json(target / 'input.json', {
            'jobId': key, 'receivedAt': datetime.now(timezone.utc).isoformat(),
            'filename': name, 'bytes': len(image), 'sha256': hashlib.sha256(image).hexdigest(),
            'state': 'received',
        })
        return target
    except (OSError, ValueError):
        log.exception('Could not archive shelf input job=%s', key)
        return None


def save_result(target: Path | None, status: int, result: dict, elapsed: float) -> None:
    if target is None:
        return
    try:
        _write_json(target / 'result.json', {
            'jobId': target.name, 'finishedAt': datetime.now(timezone.utc).isoformat(),
            'status': status, 'state': 'done' if status == 200 else 'failed',
            'elapsedSeconds': round(elapsed, 3), 'result': result,
        })
    except (OSError, ValueError, TypeError):
        log.exception('Could not archive shelf result job=%s', target.name)
