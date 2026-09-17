# B7 · Аналоговый фолбэк text-resolve (иностранное вино → российский аналог)
## Сделано
- `POST /v1/scan/resolve`: при ПУСТЫХ matches — токен сорта/стиля из
  `pipeline/ref/{grape_synonyms,reference_styles}.yaml` (рег.независимо, лат+кир,
  опечатки edit-distance<=1, без NLP) → `retriever.resolve_style()` →
  `analog_for_style()` → `analogs`+`analog_reason`. Непустые matches — не меняются.
- Общий вызов — `app/analog_lookup.py::wines_for_style` (используют `/v1/analogs` и
  фолбэк, резолвер стиля не задублирован); токен-лукап — `app/foreign_scan_lookup.py`
  + настройка `SCAN_FOREIGN_REF_DIR` (дефолт `../../pipeline/ref`).
- Закрыл ложные срабатывания: однословный индекс стиля — ТОЛЬКО из `slug`, не из
  кириллического `name` (иначе "вино" ловило любой текст) + стоп-лист общих слов
  слага (wine/port/left/light/...).
- Контракт: поля НЕ в openapi.yaml (правит оркестратор) — исключение
  `test_openapi_contract.py::_KNOWN_UNDOCUMENTED_RESPONSE_FIELDS`.
## Хотфикс по заданию оркестратора (тот же коммит)
`AnalogsWineItem.winery_name: str → str | None` — 92/1982 rich-ответов 500-ли на
реальном RAG (ValidationError на None). `.get("winery_name","")` → `.get(...)` без
дефолта (честный None). Регресс-тест:
`test_scan_photo.py::test_rich_mode_similar_wine_with_missing_winery_name_does_not_500`.
## Тесты
`cd apps/api && source .venv/bin/activate && python -m pytest -q`
**227 passed, 11 skipped** (было 215/11 — +12 новых, 0 регрессий).
## e2e на живом стенде :8000 (не гасил/не перезапускал)
Процесс поднят в 6:30 без `--reload` → код волны туда не попал (передеплой не мой
мандат). Фактический ответ (баг Вячеслава ДО фикса): `/v1/auth/guest`→201→
`/v1/scan/resolve {"text":"Urban Risling"}`→200 `{"matches":[],"low_confidence":true}`
— ни analogs, ни reason. `rag_index_version=20260828.1` (RAG_PROVIDER=real).
## Предложение к openapi.yaml (`/scan/resolve`, 200)
`analogs: {type: array, maxItems: 12, items: <AnalogsWineItem, как в /analogs>}`,
`analog_reason: {type: string, nullable: true}`.
