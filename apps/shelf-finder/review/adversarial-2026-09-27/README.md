# Regression tests from the adversarial review — 2026-09-27

The original failures were reproduced at `32ddd2f`. Tests now assert the corrected
behavior: **PASS means the reviewed defect is prevented**. The historical
[review](../../docs/adversarial-review-2026-09-27.md) records original observations;
the [fix report](../../docs/adversarial-fixes-2026-09-27.md) maps changes to findings.

Run from repository root with installed dependencies:

```bash
PYTHONPATH=apps/shelf-finder/server/src CUDA_VISIBLE_DEVICES='' \
  "${SHELF_TEST_PYTHON:-apps/shelf-finder/server/.venv/bin/python}" -m pytest -q \
  apps/shelf-finder/server/tests \
  apps/shelf-finder/review/adversarial-2026-09-27/test_attacks.py \
  apps/shelf-finder/review/adversarial-2026-09-27/test_launcher.py
node apps/web/node_modules/vitest/vitest.mjs run \
  --config apps/shelf-finder/review/adversarial-2026-09-27/frontend-vitest.config.ts
```

No `.env` is loaded. No external provider, live application, GPU, dataset, or model
weights are used. A disposable loopback HTTP fixture tests slow streaming. Launcher
PIDs/log messages are mocks: no real services are started or stopped.
