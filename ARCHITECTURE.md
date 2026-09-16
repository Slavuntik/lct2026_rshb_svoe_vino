# ARCHITECTURE — сканер российских вин («Своё Вино», ЛЦТ)

Пайплайн и границы слоёв. Принцип: сопоставление важнее распознавания — снимок у полки
всегда несовершенен, точность добирается каталогом, мульти-ракурсным индексом и
калиброванными порогами. C4-диаграммы: `docs/architecture-c4.html`.

## Слои (границы = интерфейсы в contracts/)

```
фото (камера/галерея, веб)
   │  multipart POST /v1/scan/photo          [apps/web → apps/api]
   ▼
1. НОРМАЛИЗАЦИЯ ФОТО                          packages/cv/cv/normalize.py
   EXIF-ориентация · HEIC · детект этикетки (классический CV: силуэт бутылки)
   → кроп → развёртка цилиндра → мягкая фотометрия. Флаг отключения для A/B.
   ▼
2. ИЗВЛЕЧЕНИЕ ПРИЗНАКОВ                       packages/cv/cv/encoder.py
   SigLIP 2 (env CV_MODEL), MPS/CPU автовыбор, кэш эмбеддингов.
   Тот же normalize применяется к ракурсам индекса — единый визуальный домен
   (рассинхрон доменов стоил 10 п.п. self-match).
   ▼
3. ПОИСК ПО КАТАЛОГУ                          packages/cv/cv/index.py (контракт ImageIndex)
   ANN по мульти-ракурсному индексу: на позицию — эталон + ≥20 синтетических
   3D-ракурсов (цилиндр, наклоны, блики; по мотивам arXiv:2404.08820).
   Хранилище: qdrant embedded (дев) / сеть (env IMAGE_INDEX_MODE). Версия — в манифесте.
   Схлопывание ракурсов в позиции · gap до чужой near-dup группы.
   ▼
3a. ВЕРИФИКАТОР NEAR-DUP                      packages/cv (LabelVerifier, контракт v0.4.4)
   Если топ-кандидаты — одна семья этикетки (разные год/категория): OCR мелкого
   текста запроса (год, объём) против метаданных кандидатов. Воздержание = ANN-топ.
   ▼
4. ВЫДАЧА КАРТОЧКИ                            apps/api (FastAPI, contracts/openapi.yaml + image-scan.md)
   flat (?flat=1, режим скрипта оценки): ровно {"slug":"..."}, всегда лучший slug,
     несгораемый (любая ошибка → валидный JSON), без auth.
   rich (UI): slug + card (тело GET /wines/{id}) + confidence{top1_score, gap,
     f1_top1, f1_top5} + matches[top-5] + timing_ms + not_in_catalog + similar/analogs.
   Не найдено → похожие вина и аналоги из других виноделен, честное «не найдено».
   UI: одна карточка, без экрана вариантов (apps/web, mobile-first, тема портала
   VITE_THEME=portal — токены добыты из снапшотов платформы).
   ▼
5. ДОП-ФУНКЦИОНАЛ ПОСЛЕ ПОИСКА                packages/rag + packages/llm + apps/api
   «Цифровой сомелье»: гибридный RAG (BM25+вектора, реранкер, отсечка-refusal из
   манифеста), настоящий SSE, каждый ответ с цитатами-ссылками на первоисточник.
   «Аналоги из других виноделен»: /v1/analogs — resolve_style → эталонные стили.
```

## Данные

- `pipeline/` — краулер и нормализация каталога (провенанс у каждой записи:
  source_url · fetched_at · content_hash; факт `source` отделён от вычисленного `derived`);
  готовый каталог в `pipeline/catalog`, поисковые артефакты в `pipeline/build`.
- Дамп кейса (`CASE_DATA_DIR`, в git не хранится) — источник истины slug'ов;
  наш каталог — обогащение поверх (сомелье, аналоги, гастропары).
- Индексы версионируются; eval-гейт: просевшие метрики не публикуют версию.

## Оценка качества

`qa/scan_eval.py` — F1 top-1/top-5 (macro), match-rate, p50/p95; стабильный SHA-сплит
dev/holdout (holdout не участвует в подборе настроек). `qa/mock_case_script.sh` —
репетиция скрипта кейсодержателя. Отчёт eval подключается к `GET /v1/metrics/scan`
(env CV_EVAL_REPORT_PATH). Тесты: apps/api 151+ · packages/cv 42 · qa 130+ · web 64.

## Запуск

См. README.md. Ключевые env: `IMAGE_PROVIDER=mock|real`, `IMAGE_INDEX_MODE`,
`CV_MODEL`, `CASE_DATA_DIR`, `RAG_PROVIDER`, `LLM_PROVIDER` (mock работает без ключей),
`SCAN_FLAT_DEFAULT`. Всё локально, GPU не обязателен (замеры на Apple Silicon MPS:
embed p95 24.5 мс, полный поиск p95 31 мс — SLA ≤ 3 c перекрыт с запасом).
