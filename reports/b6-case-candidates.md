# B6 — метаданные кандидатов верификатора из каталога кейса + CV_VERIFY_PROXIMITY=0.04

Контракт: image-scan.md, "Дополнения v0.4.9" (после e2e B5, reports/b5-gate-v048.md).

## Код (apps/api/, коммит 2f2fc04)
- `app/cv/case_catalog.py` (новый): `lookup(slug)` — name="{winery} {name}", vintage
  regex 19xx/20xx из слага/имени, источник case-data/slug_refs.json; лениво, кэш по пути.
- `_verify_candidates()`: `case_catalog.lookup()` первым (per-slug), фолбэк — прежний
  `get_by_id()`, когда case-data недоступны/slug ей не известен.
- `config.py`: `CV_VERIFY_PROXIMITY` 0.03 -> 0.04.
- Тесты: **215 passed, 11 skipped** (было 202/11) — +13 новых, 0 регрессий.
## E2E q2 (стенд :8000, коммит 2f2fc04; RAG real, cv_index_version=case-20260917)
`POST /v1/scan/photo` (rich, гостевой токен), `case-data/eval/queries/02eef911.webp`:
- **slug: `massandra-muskatel-belyy-belye-sorta-vinograda-beloe-sladkoe-16` (ЦЕЛЬ)**
- **ocr_verified: true**, not_in_catalog: false, timing_ms: 1993
- matches (top-5, все в пределах proximity 0.04 от top1=0.83899): portveyn-belyy-gurzuf
  0.83899 (ANN top1) · muskat-belyy-yuzhnoberezhnyy 0.82711 · muskatel-chernyy 0.81943 ·
  myshako-marselan 0.80621 · **muskatel-belyy 0.80612**
- Верификатор переставил с ANN top1 (portveyn) на верный muskatel-belyy — все 5
  слагов есть в `slug_refs.json.mapping`, `case_catalog.lookup()` отработал первым.
Вердикт: P0 из `b5-gate-v048.md` (порог + латиница) закрыт обоими пунктами задания —
воздержания нет, слаг верный. `CV_VERIFY_DEBUG` на стенде не включали (решение
оркестратора) — токен-трасса не снята, исход — по полному ответу API выше, без ретуши.

Коммиты: `2f2fc04` (apps/api: код+тесты).
