#!/usr/bin/env python3
"""Run the unchanged organizer script against the main label detector (stdlib only)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "eval" / "participant_test.sh"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".gif"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def within(root: Path, relative: str) -> Path:
    parts = Path(relative)
    if not relative or parts.is_absolute() or ".." in parts.parts or "\\" in relative:
        raise ValueError(f"Небезопасный путь датасета: {relative!r}")
    target = (root / parts).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError(f"Файл отсутствует или находится вне датасета: {relative!r}")
    return target


def validate_dataset(dataset: Path) -> tuple[Path, Path, list[dict]]:
    manifest = within(dataset, "queries.tsv")
    images = dataset / "queries"
    with manifest.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter="\t", quoting=csv.QUOTE_NONE))
    if not rows or rows[0] != ["query_id", "image_path"] or len(rows) == 1:
        raise ValueError("queries.tsv: ожидаются заголовок query_id<TAB>image_path и строки изображений")
    result, seen = [], set()
    for row in rows[1:]:
        if len(row) != 2 or not re.fullmatch(r"[A-Za-z0-9._-]+", row[0]) or row[0] in seen:
            raise ValueError("queries.tsv: некорректная строка или повтор query_id")
        seen.add(row[0])
        image = within(images, row[1])
        result.append({"query_id": row[0], "image_path": row[1], "image_sha256": sha256(image)})
    checksums = dataset / "checksums.sha256"
    if checksums.exists():
        for line in checksums.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
            if not match:
                raise ValueError("Некорректная строка checksums.sha256")
            if sha256(within(dataset, match[2])) != match[1].lower():
                raise ValueError(f"Не совпала контрольная сумма: {match[2]}")
    return manifest, images.resolve(), result


def service_health(endpoint: str) -> dict:
    url = urlsplit(endpoint)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.query or url.fragment or not url.path.endswith("/v1/eval/predict")):
        raise ValueError("Нужен HTTP(S) URL основного /v1/eval/predict без ключей, query и fragment")
    health_url = urlunsplit((url.scheme, url.netloc, url.path.removesuffix("eval/predict") + "healthz", "", ""))
    with urlopen(health_url, timeout=15) as response:
        health = json.load(response)
    if not isinstance(health, dict):
        raise ValueError("healthz вернул не JSON-объект")
    version = health.get("cv_index_version")
    if (health.get("status") != "ok" or health.get("warm") is not True or not isinstance(version, str)
            or not version.strip() or version.lower() == "unknown" or "mock" in version.lower()):
        raise ValueError("Основной CV не готов или запущен на заглушке; нужен прогретый реальный индекс")
    return {key: health.get(key) for key in ("status", "warm", "cv_index_version", "rag_index_version")}


def verify_predictions(path: Path, expected: list[dict]) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    if len(rows) != len(expected):
        raise ValueError("Число результатов не совпадает с числом изображений")
    for actual, original in zip(rows, expected):
        if not isinstance(actual, dict) or any(actual.get(key) != value for key, value in original.items()):
            raise ValueError("Результат не соответствует порядку/ID/путям/SHA-256 датасета")
        slug = actual.get("predicted_slug")
        latency = actual.get("latency_ms")
        if slug is not None and (not isinstance(slug, str) or not slug.strip()):
            raise ValueError("Некорректный predicted_slug")
        if isinstance(latency, bool) or not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0:
            raise ValueError("Некорректный latency_ms")
    return rows


def stats(rows: list[dict]) -> dict:
    times = sorted(row["latency_ms"] for row in rows)
    n = len(times)
    return {
        "images": n,
        "predicted": sum(row["predicted_slug"] is not None for row in rows),
        "null_predictions": sum(row["predicted_slug"] is None for row in rows),
        "latency_ms": {"median": (times[(n - 1) // 2] + times[n // 2]) / 2,
                       "p95_nearest_rank": times[math.ceil(n * .95) - 1], "max": times[-1]},
        "accuracy": None,
        "accuracy_note": "Эталонные ответы не предоставлены; точность и confidence считает организатор.",
    }


def output_path(value: str) -> Path:
    path = Path(value).absolute()
    if path.exists() or path.is_symlink():
        raise ValueError(f"Файл уже существует, выберите новое имя: {path}")
    return path


def prepare_folder(folder: Path, work: Path) -> tuple[Path, Path, list[dict]]:
    if not folder.is_dir():
        raise ValueError("Папка изображений не найдена")
    paths = sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*")
                   if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and not path.name.startswith("._"))
    if not paths:
        raise ValueError("В папке нет поддерживаемых изображений")
    for relative in paths:
        if any(char in relative for char in "\t\r\n"):
            raise ValueError("Имя изображения содержит табуляцию или перевод строки")
        within(folder, relative)
    # Only a temporary manifest and directory link; original images are never copied or changed.
    (work / "queries").symlink_to(folder, target_is_directory=True)
    with (work / "queries.tsv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE, quotechar=None)
        writer.writerow(["query_id", "image_path"])
        for index, relative in enumerate(paths, 1):
            writer.writerow([f"q-{index:06d}", relative])
    return validate_dataset(work)


def run(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="vinchik-manifest-") as temporary:
        return run_prepared(args, Path(temporary))


def run_prepared(args: argparse.Namespace, work: Path) -> int:
    output = output_path(args.output)
    report_name = args.report or (str(output.with_suffix(".report.json")) if args.images_dir else None)
    report = output_path(report_name) if report_name else None
    if report and output.resolve() == report.resolve():
        raise ValueError("--report и --output должны быть разными файлами")
    script = Path(args.eval_script).resolve()
    if not script.is_file():
        raise ValueError("Не найден eval-script")
    dataset = Path(args.images_dir or args.dataset).resolve()
    if args.images_dir:
        manifest, images, expected = prepare_folder(dataset, work)
    else:
        manifest, images, expected = validate_dataset(dataset)
    for destination in (output, report):
        if destination and destination.resolve().is_relative_to(dataset):
            raise ValueError("Результат сохраняйте вне исходного датасета")
    for command in ("bash", "curl", "jq", "awk", "mktemp"):
        if not shutil.which(command):
            raise ValueError(f"Не установлена команда {command}")
    if not shutil.which("sha256sum") and not shutil.which("shasum"):
        raise ValueError("Не установлены sha256sum/shasum")
    health = service_health(args.endpoint)
    print(f"Digital Rover — Свой Сомелье. Датасет: {len(expected)} фото. CV: {health['cv_index_version']}", flush=True)
    print("Запуск официального participant_test.sh; последовательно, лимит 10 с на запрос, без повторов.", flush=True)
    started = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vinchik-eval-") as temporary:
        predictions = Path(temporary) / "predictions.jsonl"
        # Keep the selected organizer script unchanged; never auto-execute scripts from a dataset.
        command = ["bash", str(script), "--images-dir", str(images), "--manifest", str(manifest),
                   "--endpoint", args.endpoint, "--output", str(predictions)]
        with subprocess.Popen(command) as process:
            while True:
                try:
                    code = process.wait(timeout=30)
                    if code:
                        raise subprocess.CalledProcessError(code, command)
                    break
                except subprocess.TimeoutExpired:
                    print(f"Прогон продолжается: {time.monotonic() - clock:.0f} с...", flush=True)
        rows = verify_predictions(predictions, expected)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as target, predictions.open("rb") as source:
            shutil.copyfileobj(source, target)
    summary = stats(rows)
    if report:
        git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
        record = {"team": "Digital Rover", "started_at": started, "elapsed_seconds": round(time.monotonic() - clock, 3),
                  "endpoint": args.endpoint, "client_git_commit": git.stdout.strip() if git.returncode == 0 else None,
                  "runner_sha256": sha256(Path(__file__)), "organizer_script_sha256": sha256(script), "manifest_sha256": sha256(manifest),
                  "predictions_sha256": sha256(output), "health_before": health, "summary": summary,
                  "input_mode": "folder" if args.images_dir else "organizer_dataset", "predictions": rows}
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Результат: {output}")
    if report:
        print(f"Отчёт: {report}")
    if summary["null_predictions"]:
        print("Есть null-предсказания: файл сохранён, проверьте сервис/таймауты/отказы. Это не успешная проверка всех фото.", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--dataset", help="Набор организатора: queries.tsv, queries/, optional checksums.sha256")
    inputs.add_argument("--images-dir", help="Обычная папка изображений; рекурсивный обход, отчёт создаётся автоматически")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/v1/eval/predict")
    parser.add_argument("--output", required=True, help="Новый файл JSONL (в Git автоматически не добавляется)")
    parser.add_argument("--eval-script", default=str(SCRIPT), help="Путь к доверенному скрипту организатора с тем же CLI; по умолчанию eval/participant_test.sh")
    parser.add_argument("--report", help="Необязательный отдельный JSON с параметрами и сводкой")
    args = parser.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        print("Прервано. Полный результат не гарантирован; выберите новое имя для следующего прогона.", file=sys.stderr)
        return 130
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Ошибка проверки: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
