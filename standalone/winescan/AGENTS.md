# Repository Guidelines

## Project Structure & Module Organization
WineScan recognizes Russian wines from label photos. Python 3.12+ code lives in `src/winescan/`: `catalog/` prepares data, `vision/` extracts features, `search/` ranks matches, `service/` exposes FastAPI, and `product/` implements recommendations. `validation/` generates synthetic scenes; `eval/` runs experiments and reports.

`tests/` contains Python tests. The Nuxt 4/TypeScript frontend lives in `web/`, with UI in `app/`, API proxy code in `server/`, and fixtures in `mocks/`. Configuration belongs in `configs/`; generated outputs belong in `artifacts/`. Consult `README.md`, `ARCHITECTURE.md`, and `docs/` for pipeline decisions and evaluation results.

## Build, Test, and Development Commands
Run backend commands from the repository root:

- `make install`: create `.venv` and install CUDA PyTorch plus ML/development dependencies.
- `make test`: run pytest without GPUs or case datasets.
- `make artifacts GPU=2`: build the catalog, embedding indexes, and local features after preparing data.
- `make serve GPU=2`: start FastAPI on port 8080 using prepared artifacts.
- `make scanner GPU=2`: evaluate the service and regenerate the results report.

For the frontend, use Node.js 22+ and run `cd web`, then `npm ci`. Use `NUXT_PUBLIC_MOCK=1 npm run dev` for a standalone demo, or `NUXT_API_BASE=http://127.0.0.1:8080 npm run dev` with the backend. Validate with `npm run typecheck` and `npm run build`.

## Coding Style & Naming Conventions
Follow surrounding code: four-space Python indentation, type hints, `snake_case` functions/modules, and `PascalCase` classes. TypeScript uses two spaces, single quotes, and no semicolons. Name Vue components in PascalCase and composables `useSomething.ts`. No formatter or linter is configured; keep formatting consistent and preserve strict TypeScript checks.

## Testing Guidelines
Use pytest files named `tests/test_*.py` and functions named `test_*`. Prefer synthetic images, temporary paths, and fake scanners over model downloads or external datasets. Run focused checks with `.venv/bin/python -m pytest tests/test_service_app.py -q`. No coverage threshold is configured. For recognition changes, report evaluation split, configuration, accuracy, and latency separately from unit tests.

## Commit & Pull Request Guidelines
Recent commits use descriptive Russian subjects explaining a change or finding; follow that style. PRs should describe the problem, behavior change, and validation commands/results. Link relevant issues and include screenshots for UI changes.

## Configuration & Integration
Keep secrets in local `.env` files using `.env.example`; exclude raw datasets and generated artifacts from commits. Select an available GPU explicitly on shared servers. Since 2026-09-24 this folder (`standalone/winescan` in the service monorepo) is the source of truth for code; `tools/sync_winescan.py` copies the package into `packages/winescan`, where the integration adapter lives. A separate research working copy pulls code from here and carries its docs back.
