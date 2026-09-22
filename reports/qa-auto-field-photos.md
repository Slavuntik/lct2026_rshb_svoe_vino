# qa-auto: 32 полевых фото (Field/) через живой API, боевая конфигурация hack-v9

## Стенд
Своя копия индекса (без `.lock`, embedded qdrant не терпит второй живой `apps/api` на одном `CV_DATA_DIR` — см. прошлый отчёт, риск №2): `packages/cv/data-d1` → scratchpad `data-d1-qa` (114 МБ).
Сервер — по образцу `infra/local-check/run-check-server.sh`, порт 8773: `CV_MODEL=siglip2-base-patch16-384`, `CV_FUSION=1 W=0.3 TEXT_SOURCE=ocr CV_OCR_ENGINE=rapid SIZES=640,960 LABEL_SIZE=1280 CV_FUSION_CROPS=2`, пороги детектора — дефолтные кода (`DEFAULT_DET_BOX_THRESH=0.3/DEFAULT_DET_UNCLIP=2.0`, уже = hack-v9).
`healthz`: `cv_index_version=case-20260921-d1-b384, warm=true`. `qa/real_photos_serve.py` глобит только `*.webp` (Field — `.jpeg`) — не его зона (ml-lead), править не стал: эквивалентный скрипт в scratchpad (`field_serve.py`/`field_eval.py`, не коммичу — не моя постоянная зона).
0 HTTP-ошибок на 62 запросах (32 flat + 22 rich-NONE + 8 rich-surelikely). Сервис остановлен, порт 8773 свободен.

## Результат (part3-field.csv, вне git)
**top-1, sure+likely (8 фото): 1/8 = 12.5%** (только F08 di-kaspiko). Промахи: F02, F04, F10, F16, F17, F21, F30 — top-5 тоже почти везде мимо (в top-5 только F04, F08 = 2/8).
В rich-режиме `not_in_catalog=True` на ВСЕХ 8 (даже верный F08!), `ocr_verified=False` везде — на полке целиком бутылка занимает малую долю кадра, текст мельче/менее чёток, чем на выделенных фото организаторов (93.5% тем же конфигом). Не похоже на баг харнесса: F16/F17 (тот же кадр) дали побитово идентичный rich-ответ; ошибок 0/62.

**unsure со слогом (2, справочно, не в top-1):** F15 — угадал `chateau-tamagne-select-blanc-brut` (правда `abrau-dyurso-...shardone-beloe-12`, мимо); F22 — угадал `abrau-dyurso-...bryut-115` (правда `inkermanskiy-zmv-...`, мимо).

**NONE (22 фото):** flat всегда отдаёт лучшую догадку (ожидаемо, в flat нет отказа). Второй прогон, rich (только эти 22): **гейт `not_in_catalog=True` сработал 22/22 (100%)** — ни одного самоуверенного неверного слага.

**Тайминги flat_ms (32 фото,** `_percentile` **как в `qa/scan_eval.py`):** min 253 / p50 750 / p95 1454 / max 2921 мс — всё внутри лимита 10 с с большим запасом.

## MD5-дубли, 100 живых фото организаторов
`case-data/real-photos/*.webp`: **100/100 уникальных MD5, дублей нет.** Отдельно перепроверил находку qa-manual по Field — F16/F17 (`photo_...04.03.49/50.jpeg`) дают одинаковый MD5, подтверждено независимо.

## Дефекты каталога (из reports/qa-manual-field-photos.md, не чинил, только перечисляю)
1. `fanagoriya-rose-kaberne-sovinon-rozovoe-polusuhoe-13` — превью «Rose.» (минимализм) vs полка «Noblesse Oblige Авторское» (герб); тот же виноград/цвет/сладость.
2. `shato-taman-grape-dance` — превью красно-бордовое vs полка сине-голубое; тот же бренд/мотив (танцующая пара), другой цвет упаковки.

## Как воспроизвести
Сервер — см. выше (env-список, порт свободный ≥8770, CV_DATA_DIR — СВОЯ копия без `.lock`).
`python field_serve.py --api http://127.0.0.1:PORT --out <served>/field32-*.jsonl [--flat-only|--only <файлы>]` (скрипт не в репозитории — воссоздаётся по докстрингу, логика 1:1 с `qa/real_photos_serve.py`, только `glob("*.jpeg")` и `--src`=`Field/`).
Прогоны: `case-data/real-photos-labels/served/field32-hack-v9-{flat,rich-none,rich-surelikely}.jsonl` (вне git).

## Риски / предложения
1. Целиковые фото полки (много бутылок, «центр» — экспертное суждение qa-manual, не всегда геометрический пиксель-центр) — принципиально другой, более тяжёлый режим, чем целевые фото организаторов; 12.5% vs 93.5% на том же конфиге. Если приватная проверка может включать такие кадры — стоит решение ml-lead/product заранее, не после сдачи.
2. Гейт `not_in_catalog` на этом стиле фото срабатывает и на honest-NONE (22/22, верно), и на ВСЕХ 8 sure+likely с вином в каталоге — на полках пользователь почти никогда не увидит уверенную одну карточку, только «возможно, одно из».
3. `qa/real_photos_serve.py`: глоб только `*.webp`, на `*.jpeg` даёт 0 строк без ошибки — тихий отказ. Предлагаю ml-lead расширить глоб (`*.webp`+`*.jpeg`+`*.jpg`), если такие прогоны будут повторяться.
