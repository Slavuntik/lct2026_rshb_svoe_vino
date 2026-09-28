"""Regression tests for deployment failures; no network or production access."""
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def run(*args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, **kwargs)


def executable(path, text):
    path.write_text('#!/bin/bash\n' + text)
    path.chmod(0o755)


def test_failed_build_never_connects_to_server(tmp_path):
    repo = tmp_path / 'repo'
    (repo / 'infra/ams3').mkdir(parents=True)
    (repo / 'apps/web').mkdir(parents=True)
    (repo / 'apps/shell/plugins/ocr-plugin').mkdir(parents=True)
    for p in ('apps/web/package.json', 'apps/shell/plugins/ocr-plugin/package.json'):
        (repo / p).write_text('{}')
    shutil.copy(ROOT / 'infra/ams3/push-release.sh', repo / 'infra/ams3/push-release.sh')
    assert run('git', 'init', str(repo)).returncode == 0
    run('git', '-C', str(repo), 'add', '.')
    assert run('git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'test').returncode == 0
    tools = tmp_path / 'bin'; tools.mkdir()
    executable(tools / 'npm', 'exit 42\n')
    marker = tmp_path / 'ssh-called'
    executable(tools / 'ssh', f'touch "{marker}"\nexit 0\n')
    result = run('bash', str(repo / 'infra/ams3/push-release.sh'), 'invalid.test', env={**os.environ, 'PATH': f'{tools}:{os.environ["PATH"]}', 'GITHUB_EVENT_NAME': ''})
    assert result.returncode == 42
    assert not marker.exists(), result.stdout + result.stderr


def test_failed_warmup_restores_previous_app_and_leaves_web(tmp_path):
    base = tmp_path / 'base'
    release = base / 'releases/new'
    for p in (base / 'app/infra/ams3', base / 'web', release / 'app/apps/api', release / 'app/infra/ams3', release / 'web'):
        p.mkdir(parents=True, exist_ok=True)
    (base / 'somelye.env').write_text('SECRET=keep\n')
    (base / 'web/index.html').write_text('old-web')
    (base / 'app/marker').write_text('old-app')
    (base / 'app/infra/ams3/deploy.sh').write_text('exit 0\n')
    (release / 'app/infra/ams3/deploy.sh').write_text('exit 1\n')
    (release / 'app/apps/api/uv.lock').touch()
    (release / 'web/index.html').write_text('new-web')
    (release / 'commit').write_text('new-sha\n')
    script = tmp_path / 'activate.sh'
    script.write_text((ROOT / 'infra/ams3/activate-release.sh').read_text().replace('BASE=/opt/somelye', f'BASE="{base}"'))
    result = run('bash', str(script), str(release))
    assert result.returncode != 0
    assert (base / 'app/marker').read_text() == 'old-app'
    assert (base / 'web/index.html').read_text() == 'old-web'
    assert (base / 'somelye.env').read_text() == 'SECRET=keep\n'
    assert (release / 'failed-app/apps/api/uv.lock').exists()


def test_ui_only_release_keeps_api_running_and_rejects_runtime_changes(tmp_path):
    import sys
    base=tmp_path/'base';release=base/'releases/ui'
    for root in (base/'app',release/'app'):
        for directory in ('apps/api','apps/shelf-finder/server','packages','pipeline','infra/ams3'):
            (root/directory).mkdir(parents=True,exist_ok=True)
        (root/'apps/api/uv.lock').write_text('same dependencies')
        (root/'apps/api/main.py').write_text('same runtime')
        (root/'infra/ams3/check-release.py').write_text('pass\n')
        (root/'infra/ams3/deploy.sh').write_text('exit 91\n')
    shutil.copy(ROOT/'infra/ams3/runtime-unchanged.py',release/'app/infra/ams3/runtime-unchanged.py')
    for root in (base,release):
        (root/'web').mkdir();(root/'web/index.html').write_text(str(root))
    (base/'somelye.env').write_text('SAME=1\n');(release/'commit').write_text('new-sha\n')
    (base/'venv/bin').mkdir(parents=True);(base/'venv/bin/python').symlink_to(sys.executable)
    tools=tmp_path/'tools';tools.mkdir();executable(tools/'curl','exit 0\n')
    script=tmp_path/'activate.sh';script.write_text((ROOT/'infra/ams3/activate-release.sh').read_text().replace('BASE=/opt/somelye',f'BASE="{base}"'))
    env={**os.environ,'SKIP_API_RESTART':'1','PATH':f'{tools}:{os.environ["PATH"]}'}
    (release/'app/apps/api/main.py').write_text('changed runtime')
    rejected=run('bash',str(script),str(release),env=env)
    assert rejected.returncode != 0
    assert (base/'app/apps/api/main.py').read_text() == 'same runtime'
    assert not (release/'previous').exists()
    (release/'app/apps/api/main.py').write_text('same runtime')
    accepted=run('bash',str(script),str(release),env=env)
    assert accepted.returncode == 0,accepted.stdout+accepted.stderr
    assert (base/'deployed-commit').read_text() == 'new-sha\n'
    assert (base/'web/index.html').read_text() == str(release)
