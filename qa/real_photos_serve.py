"""Прогон реальных фото через живой API (как скрипт кейсодержателя + rich для top-5).

На фото два запроса: POST /v1/eval/predict (flat, то, что считает их скрипт) и
POST /v1/scan/photo (rich: matches top-5, not_in_catalog, ocr_verified, timing_ms).
Выход — JSONL (вне git). Метрики считает qa/real_photos_eval.py --served <jsonl>.

  python qa/real_photos_serve.py --api http://127.0.0.1:8765 --out <jsonl>
"""
import argparse, json, mimetypes, time, uuid, urllib.request
from pathlib import Path

SRC = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos")


def post(url, path, timeout):
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return data, round((time.time() - t) * 1000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8765")
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--src", default=str(SRC), help="каталог с фото (по умолчанию — case-data/real-photos на Mac)")
    ap.add_argument("--flat-only", action="store_true", help="только /v1/eval/predict (метрика), без rich")
    a = ap.parse_args()
    out = Path(a.out)
    done = {json.loads(l)["photo"] for l in out.read_text().splitlines() if l.strip()} if out.exists() else set()
    with out.open("a", encoding="utf-8") as fh:
        for i, p in enumerate(sorted(Path(a.src).glob("*.webp")), 1):
            if p.name in done:
                continue
            row = {"photo": p.name}
            try:
                flat, row["flat_ms"] = post(f"{a.api}/v1/eval/predict", p, a.timeout)
                row["flat_slug"] = flat.get("slug") if isinstance(flat, dict) else flat[0].get("slug")
                if a.flat_only:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                    continue
                rich, row["rich_ms"] = post(f"{a.api}/v1/scan/photo", p, a.timeout)
                row.update(slug=rich.get("slug"), not_in_catalog=rich.get("not_in_catalog"),
                           ocr_verified=rich.get("ocr_verified"), timing_ms=rich.get("timing_ms"),
                           top1_score=(rich.get("confidence") or {}).get("top1_score"),
                           gap=(rich.get("confidence") or {}).get("gap"),
                           matches=rich.get("matches"))
            except Exception as e:  # noqa: BLE001 — фиксируем и идём дальше
                row["error"] = f"{type(e).__name__}: {e}"
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            if i % 10 == 0:
                print(f"{i}/100", flush=True)
    print("готово:", out)


if __name__ == "__main__":
    main()
