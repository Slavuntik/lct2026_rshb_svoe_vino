#!/usr/bin/env python3
"""nt/loadtest.py — нагрузочное тестирование сканера «фото → JSON» (apps/api).

Что делает: берёт картинки из каталога (по умолчанию `nt/dataset/`), в N потоков шлёт их
multipart'ом на указанный эндпоинт в течение заданного времени и меряет время ответа —
среднее, медиана, минимум, максимум (плюс перцентили, RPS и разбор ошибок).

Зависимостей нет: только стандартная библиотека (как qa/scan_eval.py) — запускается любым
python3.11+, венв не нужен.

Основные параметры:
    --url         адрес API (полный URL эндпоинта либо база — тогда добавится /v1/eval/predict)
    --threads     число параллельных потоков (виртуальных клиентов)
    --duration    продолжительность прогона: 30s / 2m / 1h / просто секунды

Примеры:
    # базовый прогон: 8 потоков, 60 секунд, картинки из nt/dataset
    ./loadtest.py --url http://127.0.0.1:8080 --threads 8 --duration 60s

    # плоский режим сканера + свой каталог картинок + отчёт в JSON
    ./loadtest.py --url http://api.example.com/v1/scan/photo?flat=1 \
                  --threads 16 --duration 5m --dataset ./dataset --json run.json

    # прогрев 10 с (не попадает в метрики) и ограничение темпа 20 запросов/с
    ./loadtest.py --url http://127.0.0.1:8080 --threads 4 --duration 2m --warmup 10s --rps 20

Замеры: latency = полное время запроса (отправка тела + ответ сервера + чтение тела),
измеряется time.perf_counter в потоке. Соединения переиспользуются (keep-alive), чтобы в
метрику не попадал TCP/TLS-хендшейк каждого запроса — как ведёт себя реальный клиент;
`--no-keepalive` возвращает соединение-на-запрос. Тела multipart собираются ОДИН раз на
старте (в метрику не попадает сборка запроса и чтение файлов с диска).

Статистика считается по успешным ответам (HTTP 200 + валидный JSON); ошибки не смешиваются
с успехами, а выносятся отдельным разделом отчёта с разбивкой по причинам.

Коды возврата: 0 — прогон состоялся; 2 — ошибка использования (нет картинок, кривой URL);
1 — нарушен явно заданный порог `--fail-over-p95-ms` / `--fail-under-rps` / `--fail-over-errors`.
"""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import os
import re
import secrets
import signal
import ssl
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from urllib.parse import urlsplit

TOOL_NAME = "nt-loadtest"
TOOL_VERSION = "1.0.0"

_SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET_DIR = _SCRIPT_DIR / "dataset"

# Дефолт — путь скрипта кейсодержателя (README корня, contracts/image-scan.md):
# multipart-поле `image`, ответ `{"slug": "..."}`.
DEFAULT_ENDPOINT_PATH = "/v1/eval/predict"
DEFAULT_FIELD_NAME = "image"
DEFAULT_SLA_MS = 3000.0  # case.md: SLA ответа ≤ 3 с

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}


# --------------------------------------------------------------------------------------
# Параметры прогона
# --------------------------------------------------------------------------------------


def parse_duration(text: str) -> float:
    """'30' | '30s' | '5m' | '1h' | '1m30s' -> секунды."""
    raw = str(text).strip().lower()
    if not raw:
        raise argparse.ArgumentTypeError("пустая длительность")
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        seconds = float(raw)
        if seconds < 0:
            raise argparse.ArgumentTypeError(f"длительность не может быть отрицательной: '{text}'")
        return seconds
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", raw)
    if not matches or re.sub(r"(\d+(?:\.\d+)?)\s*([hms])", "", raw).strip():
        raise argparse.ArgumentTypeError(f"не понимаю длительность '{text}' — ожидаю 30, 30s, 5m, 1h, 1m30s")
    factor = {"h": 3600.0, "m": 60.0, "s": 1.0}
    total = sum(float(value) * factor[unit] for value, unit in matches)
    if total <= 0:
        raise argparse.ArgumentTypeError(f"длительность должна быть больше нуля: '{text}'")
    return total


def resolve_url(url: str) -> str:
    """Терпимый разбор адреса: можно дать базу ('http://host:8080' или 'host:8080') —
    допишется дефолтный путь эндпоинта; можно дать полный URL с query — берётся как есть.
    """
    raw = url.strip()
    if not raw:
        raise ValueError("пустой --url")
    if "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"}:
        raise ValueError(f"поддерживаются только http/https, получено: {parts.scheme}")
    if not parts.hostname:
        raise ValueError(f"в адресе нет хоста: {url}")
    # Базой считаем и "http://host", и "http://host/v1/" — в обоих случаях дописываем путь
    # эндпоинта; всё остальное берём как есть (можно указать /v1/scan/photo?flat=1).
    path = (parts.path or "").rstrip("/")
    if path in ("", "/v1"):
        path = DEFAULT_ENDPOINT_PATH
    else:
        path = parts.path
    query = f"?{parts.query}" if parts.query else ""
    netloc = parts.netloc
    return f"{parts.scheme}://{netloc}{path}{query}"


@dataclass(frozen=True)
class Config:
    url: str
    threads: int
    duration_s: float
    warmup_s: float
    dataset_dir: Path
    field_name: str
    timeout_s: float
    rps: float | None
    keepalive: bool
    headers: dict[str, str]
    insecure: bool
    sla_ms: float
    json_path: Path | None
    quiet: bool


# --------------------------------------------------------------------------------------
# Датасет: читаем картинки в память и заранее собираем multipart-тела
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Payload:
    name: str
    size_bytes: int
    body: bytes
    content_type: str  # заголовок запроса целиком (multipart/form-data; boundary=...)


def load_payloads(dataset_dir: Path, field_name: str) -> list[Payload]:
    if not dataset_dir.is_dir():
        raise FileNotFoundError(
            f"каталог с картинками не найден: {dataset_dir}\n"
            f"создайте его и положите фото (или сгенерируйте заглушки: python3 make_dataset.py)"
        )
    files = sorted(
        p for p in dataset_dir.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not files:
        raise FileNotFoundError(
            f"в {dataset_dir} нет картинок ({', '.join(sorted(IMAGE_EXTENSIONS))}) — "
            f"положите фото или запустите: python3 make_dataset.py"
        )

    payloads: list[Payload] = []
    for path in files:
        content = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        body, boundary = build_multipart(field_name, path.name, content, mime)
        payloads.append(
            Payload(
                name=str(path.relative_to(dataset_dir)),
                size_bytes=len(content),
                body=body,
                content_type=f"multipart/form-data; boundary={boundary}",
            )
        )
    return payloads


def build_multipart(field_name: str, filename: str, content: bytes, mime: str) -> tuple[bytes, str]:
    """Тело multipart/form-data с одним файловым полем. Boundary подбирается так, чтобы
    не встречаться внутри самой картинки (иначе сервер разрежет тело не там)."""
    while True:
        boundary = f"----{TOOL_NAME}{secrets.token_hex(16)}"
        if boundary.encode() not in content:
            break
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return body, boundary


# --------------------------------------------------------------------------------------
# Замеры
# --------------------------------------------------------------------------------------


@dataclass
class Sample:
    started_at: float   # секунды от старта прогона (для отсечки прогрева и профиля RPS)
    latency_ms: float
    ok: bool
    status: int | None
    error: str | None
    image: str


@dataclass
class Counters:
    """Живой счётчик для строки прогресса (под общим замком — цена ничтожна на фоне HTTP)."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    done: int = 0
    ok: int = 0
    failed: int = 0
    latency_sum_ms: float = 0.0

    def add(self, sample: Sample) -> None:
        with self.lock:
            self.done += 1
            if sample.ok:
                self.ok += 1
                self.latency_sum_ms += sample.latency_ms
            else:
                self.failed += 1

    def snapshot(self) -> tuple[int, int, int, float]:
        with self.lock:
            avg = self.latency_sum_ms / self.ok if self.ok else 0.0
            return self.done, self.ok, self.failed, avg


class Client:
    """HTTP-клиент одного потока: keep-alive-соединение, которое переоткрывается после
    любой транспортной ошибки (иначе следующий запрос уедет в уже сломанный сокет)."""

    def __init__(self, cfg: Config):
        parts = urlsplit(cfg.url)
        self._https = parts.scheme == "https"
        self._host = parts.hostname or ""
        self._port = parts.port
        self._path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        self._timeout = cfg.timeout_s
        self._keepalive = cfg.keepalive
        self._extra_headers = cfg.headers
        self._ctx: ssl.SSLContext | None = None
        if self._https:
            self._ctx = ssl._create_unverified_context() if cfg.insecure else ssl.create_default_context()
        self._conn: HTTPConnection | HTTPSConnection | None = None

    def _connect(self) -> HTTPConnection | HTTPSConnection:
        if self._conn is None:
            if self._https:
                self._conn = HTTPSConnection(self._host, self._port, timeout=self._timeout, context=self._ctx)
            else:
                self._conn = HTTPConnection(self._host, self._port, timeout=self._timeout)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def post(self, payload: Payload) -> tuple[int, bytes]:
        conn = self._connect()
        headers = {
            "Content-Type": payload.content_type,
            "Content-Length": str(len(payload.body)),
            "Accept": "application/json",
            "Connection": "keep-alive" if self._keepalive else "close",
            "User-Agent": f"{TOOL_NAME}/{TOOL_VERSION}",
            **self._extra_headers,
        }
        try:
            conn.request("POST", self._path, body=payload.body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()  # тело обязательно дочитать целиком, иначе соединение не переиспользовать
            status = resp.status
        except Exception:
            self.close()
            raise
        if not self._keepalive or resp.will_close:
            self.close()
        return status, data


def run_request(client: Client, payload: Payload, t0: float) -> Sample:
    started = time.perf_counter()
    try:
        status, data = client.post(payload)
    except TimeoutError:
        return Sample(started - t0, (time.perf_counter() - started) * 1000, False, None, "timeout", payload.name)
    except Exception as exc:  # транспорт: обрыв, отказ в соединении, DNS, TLS
        detail = f"{type(exc).__name__}: {exc}".strip()
        return Sample(started - t0, (time.perf_counter() - started) * 1000, False, None, detail[:200], payload.name)
    latency_ms = (time.perf_counter() - started) * 1000

    if status != 200:
        return Sample(started - t0, latency_ms, False, status, f"HTTP {status}", payload.name)
    try:
        json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return Sample(started - t0, latency_ms, False, status, f"ответ не JSON: {exc}", payload.name)
    return Sample(started - t0, latency_ms, True, status, None, payload.name)


def worker(
    index: int,
    cfg: Config,
    payloads: list[Payload],
    t0: float,
    deadline: float,
    stop: threading.Event,
    out: list[Sample],
    counters: Counters,
) -> None:
    client = Client(cfg)
    # Потоки делят датасет чересполосно (поток i шлёт i, i+N, i+2N…): одна и та же картинка
    # не уходит от N потоков подряд (иначе кэши сервера показали бы нереалистично хорошую картину).
    cursor = index
    interval = cfg.threads / cfg.rps if cfg.rps else 0.0  # пауза между запросами ОДНОГО потока
    next_at = time.monotonic()
    try:
        while not stop.is_set():
            now = time.monotonic()
            if now >= deadline:
                break
            if interval:
                if next_at > now:
                    if stop.wait(min(next_at - now, deadline - now)):
                        break
                    continue  # после паузы заново проверить дедлайн — иначе запрос уйдёт после него
                next_at = max(next_at + interval, time.monotonic() - interval)
            payload = payloads[cursor % len(payloads)]
            cursor += cfg.threads
            sample = run_request(client, payload, t0)
            out.append(sample)  # list.append атомарен (GIL) — свой список на поток, без замка
            counters.add(sample)
            if sample.status is None and not sample.ok:
                # Транспортная ошибка (сервер не поднят, соединение отвергнуто): без паузы
                # поток крутил бы десятки тысяч мгновенных отказов в секунду и раздувал отчёт.
                if stop.wait(0.05):
                    break
    finally:
        client.close()


def progress_printer(cfg: Config, counters: Counters, t0: float, total_s: float, stop: threading.Event) -> None:
    prev_done = 0
    prev_t = time.perf_counter()
    while not stop.wait(1.0):
        now = time.perf_counter()
        done, ok, failed, avg = counters.snapshot()
        window = now - prev_t
        rps = (done - prev_done) / window if window > 0 else 0.0
        prev_done, prev_t = done, now
        elapsed = now - t0
        phase = "прогрев" if elapsed < cfg.warmup_s else "замер"
        sys.stderr.write(
            f"\r[{elapsed:6.0f}с/{total_s:.0f}с {phase}] запросов: {done:<7} ок: {ok:<7} "
            f"ошибок: {failed:<5} rps: {rps:7.1f} среднее: {avg:7.1f} мс   "
        )
        sys.stderr.flush()
    sys.stderr.write("\r" + " " * 110 + "\r")
    sys.stderr.flush()


# --------------------------------------------------------------------------------------
# Метрики
# --------------------------------------------------------------------------------------


def percentile(sorted_values: list[float], q: float) -> float:
    """Перцентиль по ближайшему рангу (без интерполяции) — устойчиво на малых выборках."""
    if not sorted_values:
        return 0.0
    rank = max(1, min(len(sorted_values), math.ceil(q / 100.0 * len(sorted_values))))
    return sorted_values[rank - 1]


def summarize(samples: list[Sample], cfg: Config, wall_s: float) -> dict:
    ok_samples = [s for s in samples if s.ok]
    failed = [s for s in samples if not s.ok]
    lat = sorted(s.latency_ms for s in ok_samples)

    latency: dict[str, float | None] = {
        "min_ms": None, "avg_ms": None, "median_ms": None, "max_ms": None,
        "p90_ms": None, "p95_ms": None, "p99_ms": None, "stdev_ms": None,
    }
    if lat:
        latency = {
            "min_ms": lat[0],
            "avg_ms": statistics.fmean(lat),
            "median_ms": statistics.median(lat),
            "max_ms": lat[-1],
            "p90_ms": percentile(lat, 90),
            "p95_ms": percentile(lat, 95),
            "p99_ms": percentile(lat, 99),
            "stdev_ms": statistics.stdev(lat) if len(lat) > 1 else 0.0,
        }

    errors: dict[str, int] = {}
    for s in failed:
        key = s.error or "неизвестная ошибка"
        errors[key] = errors.get(key, 0) + 1

    within_sla = sum(1 for v in lat if v <= cfg.sla_ms)
    total = len(samples)

    return {
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "url": cfg.url,
            "threads": cfg.threads,
            "duration_s": cfg.duration_s,
            "warmup_s": cfg.warmup_s,
            "dataset_dir": str(cfg.dataset_dir),
            "field_name": cfg.field_name,
            "timeout_s": cfg.timeout_s,
            "rps_limit": cfg.rps,
            "keepalive": cfg.keepalive,
            "sla_ms": cfg.sla_ms,
        },
        "measure_window_s": wall_s,
        "requests": {
            "total": total,
            "ok": len(ok_samples),
            "failed": len(failed),
            "error_rate": (len(failed) / total) if total else 0.0,
        },
        "throughput_rps": (len(ok_samples) / wall_s) if wall_s > 0 else 0.0,
        "latency": latency,
        "sla": {
            "threshold_ms": cfg.sla_ms,
            "within": within_sla,
            "share": (within_sla / len(lat)) if lat else 0.0,
        },
        "errors": dict(sorted(errors.items(), key=lambda kv: -kv[1])),
    }


def _ms(value: float | None) -> str:
    return "—" if value is None else f"{value:9.1f} мс"


def render_report(summary: dict, payloads: list[Payload]) -> str:
    cfg = summary["config"]
    req = summary["requests"]
    lat = summary["latency"]
    sizes = [p.size_bytes for p in payloads]
    lines = [
        "",
        "=" * 64,
        "  НАГРУЗОЧНЫЙ ТЕСТ — РЕЗУЛЬТАТ",
        "=" * 64,
        f"  эндпоинт           {cfg['url']}",
        f"  потоков            {cfg['threads']}"
        + (f"   (лимит {cfg['rps_limit']} rps)" if cfg["rps_limit"] else ""),
        f"  окно замера        {summary['measure_window_s']:.1f} с  (план {cfg['duration_s']:.0f} с"
        + (f" + прогрев {cfg['warmup_s']:.0f} с, отброшен" if cfg["warmup_s"] else "") + ")",
        f"  датасет            {len(payloads)} картинок, "
        f"{min(sizes) / 1024:.0f}–{max(sizes) / 1024:.0f} КБ ({cfg['dataset_dir']})",
        "-" * 64,
        f"  запросов всего     {req['total']}",
        f"  успешных           {req['ok']}",
        f"  ошибок             {req['failed']}   ({req['error_rate'] * 100:.2f} %)",
        f"  пропускная способ. {summary['throughput_rps']:.1f} запросов/с",
        "-" * 64,
        "  ВРЕМЯ ОТВЕТА (по успешным)",
        f"    среднее          {_ms(lat['avg_ms'])}",
        f"    медиана          {_ms(lat['median_ms'])}",
        f"    минимум          {_ms(lat['min_ms'])}",
        f"    максимум         {_ms(lat['max_ms'])}",
        f"    p90 / p95 / p99  {_ms(lat['p90_ms'])} / {_ms(lat['p95_ms'])} / {_ms(lat['p99_ms'])}",
        f"    ст. отклонение   {_ms(lat['stdev_ms'])}",
    ]
    sla = summary["sla"]
    if req["ok"]:
        lines.append(
            f"    в SLA ≤ {sla['threshold_ms']:.0f} мс   {sla['within']} из {req['ok']}"
            f"  ({sla['share'] * 100:.1f} %)"
        )
    if req["ok"] == 0:
        lines.append("  ни одного успешного ответа — проверьте адрес, порт и доступность сервиса")
    if summary["errors"]:
        lines += ["-" * 64, "  ОШИБКИ"]
        for reason, count in summary["errors"].items():
            lines.append(f"    {count:>7}  {reason}")
    lines += ["=" * 64, ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loadtest.py",
        description="Нагрузочное тестирование API «картинка → JSON»: картинки из каталога, "
                    "N потоков, заданная продолжительность; на выходе — среднее, медианное, "
                    "максимальное и минимальное время ответа.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", required=True,
                        help=f"адрес API: полный URL эндпоинта или база (тогда путь = {DEFAULT_ENDPOINT_PATH})")
    parser.add_argument("--threads", "-t", type=int, default=4, help="число параллельных потоков")
    parser.add_argument("--duration", "-d", type=parse_duration, default="60s",
                        help="продолжительность замера: 30, 30s, 5m, 1h, 1m30s")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_DIR,
                        help="каталог с картинками (рекурсивно)")
    parser.add_argument("--warmup", type=parse_duration, default="0",
                        help="прогрев: запросы в этом окне не попадают в метрики")
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME, help="имя multipart-поля с файлом")
    parser.add_argument("--timeout", type=float, default=30.0, help="таймаут запроса, секунд")
    parser.add_argument("--rps", type=float, default=None,
                        help="ограничить общий темп (запросов/с); по умолчанию — без ограничения")
    parser.add_argument("--no-keepalive", action="store_true",
                        help="новое соединение на каждый запрос (в latency войдёт хендшейк)")
    parser.add_argument("--header", "-H", action="append", default=[], metavar="'Name: value'",
                        help="дополнительный заголовок (можно повторять), например авторизация")
    parser.add_argument("--insecure", "-k", action="store_true", help="не проверять TLS-сертификат")
    parser.add_argument("--sla-ms", type=float, default=DEFAULT_SLA_MS,
                        help="порог SLA для доли уложившихся ответов (case.md: 3 с)")
    parser.add_argument("--json", type=Path, default=None, metavar="PATH",
                        help="записать машинный отчёт (JSON) по этому пути")
    parser.add_argument("--quiet", "-q", action="store_true", help="без строки прогресса")
    parser.add_argument("--fail-over-p95-ms", type=float, default=None,
                        help="выйти с кодом 1, если p95 больше порога (для CI)")
    parser.add_argument("--fail-under-rps", type=float, default=None,
                        help="выйти с кодом 1, если пропускная способность ниже порога")
    parser.add_argument("--fail-over-errors", type=float, default=None, metavar="SHARE",
                        help="выйти с кодом 1, если доля ошибок выше порога (0..1)")
    return parser


def parse_headers(raw: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in raw:
        if ":" not in item:
            raise ValueError(f"заголовок должен быть в виде 'Name: value', получено: {item!r}")
        name, _, value = item.partition(":")
        headers[name.strip()] = value.strip()
    return headers


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.threads < 1:
        print("ошибка: --threads должен быть ≥ 1", file=sys.stderr)
        return 2
    if args.rps is not None and args.rps <= 0:
        print("ошибка: --rps должен быть больше нуля", file=sys.stderr)
        return 2
    try:
        cfg = Config(
            url=resolve_url(args.url),
            threads=args.threads,
            duration_s=args.duration,
            warmup_s=args.warmup,
            dataset_dir=args.dataset.expanduser().resolve(),
            field_name=args.field,
            timeout_s=args.timeout,
            rps=args.rps,
            keepalive=not args.no_keepalive,
            headers=parse_headers(args.header),
            insecure=args.insecure,
            sla_ms=args.sla_ms,
            json_path=args.json,
            quiet=args.quiet,
        )
        payloads = load_payloads(cfg.dataset_dir, cfg.field_name)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2

    total_s = cfg.warmup_s + cfg.duration_s
    total_mb = sum(p.size_bytes for p in payloads) / 1024 / 1024
    print(
        f"{TOOL_NAME} {TOOL_VERSION}: {cfg.url}\n"
        f"  датасет: {len(payloads)} картинок ({total_mb:.1f} МБ) из {cfg.dataset_dir}\n"
        f"  потоков: {cfg.threads}, прогрев: {cfg.warmup_s:.0f} с, замер: {cfg.duration_s:.0f} с"
        + (f", лимит: {cfg.rps} rps" if cfg.rps else "") + "\n",
        file=sys.stderr,
    )

    stop = threading.Event()

    def on_signal(signum, frame):  # noqa: ARG001 — сигнатура обработчика
        sys.stderr.write("\nпрерывание — останавливаю потоки, считаю то, что успели собрать…\n")
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    counters = Counters()
    buckets: list[list[Sample]] = [[] for _ in range(cfg.threads)]
    t0 = time.perf_counter()
    deadline = time.monotonic() + total_s

    progress: threading.Thread | None = None
    if not cfg.quiet and sys.stderr.isatty():
        progress = threading.Thread(target=progress_printer, args=(cfg, counters, t0, total_s, stop), daemon=True)
        progress.start()

    threads = [
        threading.Thread(target=worker, args=(i, cfg, payloads, t0, deadline, stop, buckets[i], counters), daemon=True)
        for i in range(cfg.threads)
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    wall_s = time.perf_counter() - t0
    stop.set()
    if progress is not None:
        progress.join(timeout=2.0)

    all_samples = [s for bucket in buckets for s in bucket]
    if wall_s > total_s + 1.0 and not cfg.quiet:
        print(
            f"замечание: прогон шёл {wall_s:.1f} с вместо {total_s:.0f} с — запросы, начатые до "
            f"дедлайна, дочитывались после него (медленные ответы/таймауты). RPS считается по "
            f"фактическому окну.",
            file=sys.stderr,
        )
    measured = [s for s in all_samples if s.started_at >= cfg.warmup_s]
    window_s = max(wall_s - cfg.warmup_s, 1e-9)

    if not measured:
        print(
            "ошибка: ни одного запроса в окне замера — сервер недоступен или прогон слишком короткий",
            file=sys.stderr,
        )
        if all_samples:
            print(f"  за прогрев сделано запросов: {len(all_samples)}", file=sys.stderr)
        return 2

    summary = summarize(measured, cfg, window_s)
    print(render_report(summary, payloads))

    if cfg.json_path is not None:
        cfg.json_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"машинный отчёт: {cfg.json_path}", file=sys.stderr)

    if summary["requests"]["ok"] == 0:
        return 2  # сервис недоступен / не тот адрес: ни одного успешного ответа

    failures = []
    p95 = summary["latency"]["p95_ms"]
    if args.fail_over_p95_ms is not None and p95 is not None and p95 > args.fail_over_p95_ms:
        failures.append(f"p95 {p95:.1f} мс > порога {args.fail_over_p95_ms:.1f} мс")
    if args.fail_under_rps is not None and summary["throughput_rps"] < args.fail_under_rps:
        failures.append(f"rps {summary['throughput_rps']:.1f} < порога {args.fail_under_rps:.1f}")
    if args.fail_over_errors is not None and summary["requests"]["error_rate"] > args.fail_over_errors:
        failures.append(
            f"доля ошибок {summary['requests']['error_rate'] * 100:.2f} % > порога "
            f"{args.fail_over_errors * 100:.2f} %"
        )
    if failures:
        for item in failures:
            print(f"ПОРОГ НАРУШЕН: {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
