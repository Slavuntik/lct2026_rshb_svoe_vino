"""Exercise the real shelf HTTP API; no manual labels are sent to the service."""

import argparse
import json
from pathlib import Path
import time
import httpx


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:8086")
    p.add_argument("--photos", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--files", nargs="+", required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = {"runtime": "native HTTP API", "manualReview": False, "rows": []}
    with httpx.Client(base_url=args.url, timeout=180) as client:
        client.get("/v1/shelf/health").raise_for_status()
        for name in args.files:
            started = time.perf_counter()
            with (args.photos / name).open("rb") as file:
                response = client.post(
                    "/v1/shelf/scan", files={"image": (name, file, "image/jpeg")}
                )
            response.raise_for_status()
            result = response.json()
            row = {"file": name, "httpSeconds": time.perf_counter() - started, **result}
            results["rows"].append(row)
            (args.output / "results.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2)
            )
            print(
                json.dumps({k: v for k, v in row.items() if k != "matches"}), flush=True
            )


if __name__ == "__main__":
    main()
