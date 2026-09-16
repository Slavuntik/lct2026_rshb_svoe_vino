#!/usr/bin/env python3
"""qa/mock_scan_server.py — минимальный HTTP-мок `POST /v1/scan/photo` (агент F).

Нужен для двух вещей, которые сегодня (датасет и скрипт кейса ещё не приехали, apps/api ещё
не реализует /v1/scan/photo — см. contracts/image-scan.md, agents/G-cv.md) нельзя проверить
против настоящего стека:

  1. `qa/mock_case_script.sh` — рехёрсал bash-скрипта кейсодержателя (case.md, п.6:
     последовательные POST фото, плоский `{"slug": "..."}`, скрипт сам меряет время) —
     нужен РЕАЛЬНЫЙ слушающий HTTP-сервер, не in-process заглушка.
  2. `qa/scan_eval.py --mode flat|rich --api-url http://localhost:<port>` — dry-run того же
     HTTP-клиента/парсинга, что пойдёт в бой против настоящего apps/api, без зависимости от
     ещё не написанного CV-ядра.

Никакого CV внутри: ответ — по имени файла из multipart (`filename=...`), через карту
`--map <json>` (`{photo_id: slug | [slug, ...]}`, как `--mock-map` у scan_eval.py) либо, если
имя не в карте, — slug выводится тем же терпимым правилом, что и `scan_eval.load_eval_set`
(`infer_slug_from_filename`) — то есть по умолчанию сервер ведёт себя как «идеальный оракул»
на файлах, названных по конвенции raннера (`<slug>.jpg` / `<slug>__что-то.jpg`), и как
управляемый источник неверных ответов там, где это явно нужно (карта).

Задержка — сид-детерминированный джиттер в диапазоне `--min-latency-ms`/`--max-latency-ms»`
(по умолчанию 50-250мс, заведомо ниже SLA кейса в 3с) — чтобы p50/p95 в рехёрсале не были
вырожденными нулями.

Использование:
    python mock_scan_server.py --port 8100 --map qa/tests/fixtures/scan_mini/mock_map.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from scan_eval import IMAGE_FIELD_NAME, infer_slug_from_filename  # тот же каталог qa/

FALLBACK_SLUG = "__mock_unknown__"


def _normalize_slug_map(raw: dict[str, object]) -> dict[str, list[str]]:
    """Терпимо к обеим формам значения — `"slug"` (укорочение для однослотового топ-5) и
    `["slug", ...]` — используется и для карты из файла (`--map`), и для карты, переданной
    напрямую в `serve()` (тесты) — одна нормализация, не две расходящиеся копии."""
    out: dict[str, list[str]] = {}
    for photo_id, value in raw.items():
        out[photo_id] = [value] if isinstance(value, str) else list(value)  # type: ignore[list-item]
    return out


def parse_multipart(content_type: str, body: bytes) -> tuple[str, bytes]:
    """Разбирает multipart/form-data, собранный `scan_eval._build_multipart` (одна файловая
    часть) — не претендует на полноту RFC 2046, только на round-trip со своим же кодировщиком
    (см. test_scan_eval.py — encode/decode проверяются парой)."""
    if "boundary=" not in content_type:
        raise ValueError("multipart Content-Type без boundary")
    boundary = content_type.split("boundary=", 1)[1].strip()
    if boundary.startswith('"') and boundary.endswith('"'):
        boundary = boundary[1:-1]
    marker = ("--" + boundary).encode()
    for raw_part in body.split(marker):
        # ВАЖНО: bytes.strip(b"\r\n") стрипает КЛАСС символов (любой \r или \n с краёв,
        # сколько бы их ни было подряд), а не буквальную подстроку — это съело бы часть
        # содержимого файла, если оно само оканчивается на \r\n (нашли этим же тестом,
        # см. test_scan_eval.py::test_multipart_encode_decode_roundtrip). Структура
        # multipart даёт РОВНО один ведущий и один замыкающий \r\n вокруг каждой части —
        # снимаем их срезом по 2 байта, не strip().
        part = raw_part
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        header_blob, _, content = part.partition(b"\r\n\r\n")
        headers = header_blob.decode("latin-1", errors="replace")
        if "filename=" not in headers.lower():
            continue
        filename = "upload.bin"
        for line in headers.split("\r\n"):
            if line.lower().startswith("content-disposition") and "filename=" in line:
                for token in line.split(";"):
                    token = token.strip()
                    if token.startswith("filename="):
                        filename = token.split("=", 1)[1].strip().strip('"')
        return filename, content
    raise ValueError("multipart: файловая часть не найдена")


def make_handler(
    slug_map: dict[str, object],
    min_latency_ms: float,
    max_latency_ms: float,
    rng: random.Random,
    *,
    legacy_no_matches: bool = False,
) -> type[BaseHTTPRequestHandler]:
    """`legacy_no_matches=True` — не класть поле `matches` в rich-ответ вовсе, имитируя
    apps/api старее v0.4.3 (до `a2bc591`) — нужно только тестам фолбэка `RichApiPredictor`
    на `similar`; CLI/рехёрсал по умолчанию всегда шлют `matches` (текущий контракт)."""
    slug_map = _normalize_slug_map(slug_map)
    class ScanPhotoHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:  # тише в тестах/CLI-рехёрсале
            pass

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — сигнатура BaseHTTPRequestHandler
            path = urlsplit(self.path).path
            if path == "/v1/healthz":
                self._send_json(200, {"status": "ok", "index_version": "mock"})
                return
            if path == "/v1/metrics/scan":
                # Зеркалит apps/api/app/routers/metrics.py::scan_metrics до первого
                # eval-прогона: index_version есть всегда, f1_*/match_rate честно null —
                # этот мок сам ничего не оценивает, только отвечает на /scan/photo.
                self._send_json(
                    200,
                    {"index_version": "mock", "f1_top1": None, "f1_top5": None, "match_rate": None,
                     "eval_set": None, "measured_at": None},
                )
                return
            self._send_json(404, {"error": {"code": "not_found", "message": "не найдено"}})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path != "/v1/scan/photo":
                self._send_json(404, {"error": {"code": "not_found", "message": "не найдено"}})
                return
            flat = parse_qs(parsed.query).get("flat", ["0"])[0] == "1"
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            content_type = self.headers.get("Content-Type", "")
            try:
                filename, _content = parse_multipart(content_type, body)
            except ValueError as exc:
                self._send_json(422, {"error": {"code": "validation_error", "message": str(exc)}})
                return

            # Карта со значением [] (пустой список, а не отсутствующий ключ) — намеренный
            # "не нашли": даёт возможность детерминированно рехёрсить честный not_in_catalog/
            # пустой-slug путь (см. contracts/image-scan.md и найденный на живом apps/api
            # B баг: пустой/null slug — ВАЛИДНЫЙ ответ, не ошибка транспорта — test_scan_eval.py
            # ::test_flat_api_predictor_empty_slug_is_a_valid_miss_not_an_error и rich-аналог).
            top5 = slug_map.get(filename)
            if top5 is None:
                inferred = infer_slug_from_filename(filename)
                top5 = [inferred] if inferred else [FALLBACK_SLUG]

            latency_ms = rng.uniform(min_latency_ms, max_latency_ms)
            time.sleep(latency_ms / 1000)

            # FALLBACK_SLUG как ПЕРВЫЙ элемент — явный сигнал "not_in_catalog, но с
            # реальными ANN-кандидатами дальше в списке" (contracts/image-scan.md v0.4.3:
            # PhotoScanResult.matches заполняется НЕЗАВИСИМО от confident-решения) —
            # отличается от top5=[] ("совсем ничего не нашли", slug=null И matches тоже
            # пуст). Нужно для test_rich_api_predictor_not_in_catalog_still_reports_matches_
            # for_top5 — калибровка CV_CONFIDENT_SCORE_THRESHOLD смотрит именно на эту
            # комбинацию (qa/acceptance.md, «Порядок дня датасета»).
            if top5 and top5[0] == FALLBACK_SLUG:
                slug = None
                candidates = top5[1:]
            else:
                slug = top5[0] if top5 else None
                candidates = top5[1:] if top5 else []

            if flat:
                self._send_json(200, {"slug": slug or ""})
                return
            rich_body = {
                "slug": slug,
                "card": None,
                "confidence": {
                    "top1_score": 0.9 if slug else None,
                    "gap": 0.2 if slug else None,
                    "f1_top1": None,
                    "f1_top5": None,
                },
                "ocr_verified": False,
                "timing_ms": latency_ms,
                "not_in_catalog": slug is None,
                # apps/api/app/schemas.py::AnalogsWineItem — поле называется wine_id, не
                # slug (в этой системе они совпадают по значению, но не по имени ключа) —
                # мок должен зеркалить настоящую форму, а не удобную для себя.
                "similar": [{"wine_id": s, "name": s, "winery_name": "", "region_name": ""} for s in candidates],
                "analogs": [],
            }
            if not legacy_no_matches:
                # contracts/image-scan.md v0.4.3 (apps/api commit a2bc591, пробел нашёл F):
                # top-5 схлопнутых позиций {slug, score} по убыванию, заполняется
                # НЕЗАВИСИМО от confident/not_in_catalog — честные синтетические убывающие
                # скоры, реальные числа мок не считает (у него нет модели).
                matches_source = ([slug] if slug else []) + candidates
                rich_body["matches"] = [
                    {"slug": s, "score": round(0.9 - i * 0.05, 4)} for i, s in enumerate(matches_source[:5])
                ]
            self._send_json(200, rich_body)

    return ScanPhotoHandler


def serve(
    port: int,
    slug_map: dict[str, list[str]],
    min_latency_ms: float,
    max_latency_ms: float,
    seed: int,
    *,
    legacy_no_matches: bool = False,
) -> ThreadingHTTPServer:
    handler = make_handler(slug_map, min_latency_ms, max_latency_ms, random.Random(seed), legacy_no_matches=legacy_no_matches)
    server = ThreadingHTTPServer(("localhost", port), handler)
    return server


def _load_map(path: str | None) -> dict[str, list[str]]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return _normalize_slug_map(raw)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Мок POST /v1/scan/photo для рехёрсала (агент F)")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--map", default=None, help="JSON {photo_id: slug|[slug,...]}")
    parser.add_argument("--min-latency-ms", type=float, default=50.0)
    parser.add_argument("--max-latency-ms", type=float, default=250.0)
    parser.add_argument("--seed", type=int, default=1337)
    args = parser.parse_args(argv)

    slug_map = _load_map(args.map)
    server = serve(args.port, slug_map, args.min_latency_ms, args.max_latency_ms, args.seed)
    print(f"[mock_scan_server] слушаю http://localhost:{args.port} (POST /v1/scan/photo, GET /v1/healthz)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
