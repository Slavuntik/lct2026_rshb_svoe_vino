"""Guard an explicitly requested UI-only release. Exit nonzero on runtime changes."""
from pathlib import Path
import hashlib
import sys

PATHS = ('apps/api', 'apps/shelf-finder/server', 'packages', 'pipeline')
IGNORE = {'__pycache__', '.pytest_cache', '.ruff_cache', '.venv', 'node_modules', '.DS_Store', '.embed_cache'}


def snapshot(root):
    found = {}
    for prefix in PATHS:
        base = root / prefix
        if not base.is_dir():
            raise ValueError(f'Missing runtime tree: {prefix}')
        for path in base.rglob('*'):
            if any(part in IGNORE or part.endswith('.egg-info') for part in path.relative_to(root).parts):
                continue
            if path.is_file():
                found[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).digest()
    return found


if __name__ == '__main__':
    if snapshot(Path(sys.argv[1])) != snapshot(Path(sys.argv[2])):
        sys.exit('Runtime changed; a UI-only activation is not allowed')
