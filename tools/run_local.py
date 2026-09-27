#!/usr/bin/env python3
"""Run the web app, main API and isolated shelf service from one local entry point."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from dotenv import dotenv_values
from shelf_settings import shelf_target

ROOT = Path(__file__).resolve().parents[1]
env = {**{k: v for k, v in dotenv_values(ROOT / '.env').items() if v is not None}, **os.environ}
python = env.get('VINCHIK_PYTHON', sys.executable)
api_port = int(env.get('VINCHIK_API_PORT', '8087'))
web_port = int(env.get('VINCHIK_WEB_PORT', '5173'))
shelf_port = int(env.get('SHELF_PORT', '8086'))
shelf_url, remote_shelf = shelf_target(env, shelf_port)
logs = ROOT / '.local' / 'vinchik'
logs.mkdir(parents=True, exist_ok=True)
children: list[subprocess.Popen] = []
handles = []


def health(port, path, origin=None):
    try:
        with urlopen((origin or f'http://127.0.0.1:{port}') + path, timeout=5) as response:
            return json.load(response)
    except Exception:
        return None


def require_free(port):
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1', port)) == 0:
            raise RuntimeError(f'Port {port} is occupied by another service; choose another port in .env')


def spawn(name, command, cwd, overrides):
    handle = (logs / f'{name}.log').open('a')
    handles.append(handle)
    process = subprocess.Popen(command, cwd=cwd, env={**env, **overrides}, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    children.append(process)
    print(f'{name}: PID {process.pid}; log {logs / (name + ".log")}', flush=True)
    return process


def await_ready(process, port, path, valid, seconds=600):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Service exited; inspect the local log')
        result = health(port, path)
        if result and valid(result):
            return
        time.sleep(1)
    raise RuntimeError(f'Service on {port} did not become ready; inspect its log')


def stop(*_):
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, stop)
try:
    print('API providers: RAG=' + env.get('RAG_PROVIDER', 'mock') + ', image=' + env.get('IMAGE_PROVIDER', 'mock') + ', LLM=' + env.get('LLM_PROVIDER', 'mock'), flush=True)
    require_free(web_port)
    existing_api = health(api_port, '/v1/healthz')
    existing_shelf = health(shelf_port, '/v1/shelf/health', shelf_url)
    if not existing_api:
        require_free(api_port)
    if remote_shelf and not (existing_shelf and existing_shelf.get('ready')):
        raise RuntimeError('Remote shelf is unavailable or warming; check VINCHIK_SHELF_URL and /v1/shelf/health')
    if not remote_shelf and not existing_shelf:
        require_free(shelf_port)
    if not remote_shelf and not (ROOT / 'apps/shelf-finder/dist/index.html').exists():
        subprocess.run(['npm', 'run', 'build'], cwd=ROOT / 'apps/shelf-finder', check=True)
    if not remote_shelf and existing_shelf and existing_shelf.get('ready'):
        wanted_profile = env.get('SHELF_PROFILE', 'baseline')
        wanted_device = env.get('SHELF_DEVICE', 'auto')
        if (existing_shelf.get('profile', 'baseline') != wanted_profile
                or (wanted_device == 'cpu' and existing_shelf.get('device') != 'cpu')):
            raise RuntimeError('Existing shelf service uses another profile/device; restart it to apply .env changes')
    if existing_shelf and existing_shelf.get('ready'):
        print('Reusing ready remote shelf API' if remote_shelf else f'Reusing ready shelf API on {shelf_port}', flush=True)
    else:
        shelf = spawn('shelf-api', [python, '-m', 'uvicorn', 'shelf_api.app:app', '--host', '127.0.0.1', '--port', str(shelf_port)], ROOT / 'apps/shelf-finder', {
            'PYTHONPATH': str(ROOT / 'apps/shelf-finder/server/src'),
            'CUDA_VISIBLE_DEVICES': '' if env.get('SHELF_DEVICE') == 'cpu' else env.get('SHELF_GPU', '2'),
            'SHELF_STATIC_DIR': str(ROOT / 'apps/shelf-finder/dist'),
            'OMP_NUM_THREADS': '4', 'OPENBLAS_NUM_THREADS': '4',
        })
        await_ready(shelf, shelf_port, '/v1/shelf/health', lambda r: r.get('ready'))
    if existing_api and existing_api.get('status') == 'ok' and existing_api.get('warm'):
        print(f'Reusing ready Vinchik API on {api_port}', flush=True)
    else:
        api = spawn('api', [python, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(api_port)], ROOT / 'apps/api', {
            'PYTHONPATH': os.pathsep.join(str(ROOT / p) for p in ['packages/winescan', 'packages/cv', 'packages/rag', 'packages/llm']),
            'CUDA_VISIBLE_DEVICES': env.get('VINCHIK_GPU', '3'),
            'DATABASE_URL': env.get('DATABASE_URL', 'sqlite:///' + str(logs / 'vinchik.db')),
            'OMP_NUM_THREADS': '4', 'OPENBLAS_NUM_THREADS': '4',
        })
        await_ready(api, api_port, '/v1/healthz', lambda r: r.get('status') == 'ok' and r.get('warm'))
    web = spawn('web', ['npm', 'run', 'dev', '--', '--host', '0.0.0.0', '--port', str(web_port), '--strictPort'], ROOT / 'apps/web', {
        'VITE_API_MODE': 'real',
        'VINCHIK_API_URL': f'http://127.0.0.1:{api_port}',
        'VINCHIK_SHELF_URL': shelf_url,
    })
    await_ready(web, web_port, '/v1/healthz', lambda r: r.get('status') == 'ok', seconds=30)
    print(f'Vinchik ready: http://localhost:{web_port}/app/ — shelf tab: /app/shelf', flush=True)
    print('Ctrl-C stops processes started here; reused services stay running.', flush=True)
    while all(p.poll() is None for p in children):
        time.sleep(1)
    raise RuntimeError('A local service exited; inspect logs')
except KeyboardInterrupt:
    pass
finally:
    for process in reversed(children):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
    for process in children:
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
    for handle in handles:
        handle.close()
