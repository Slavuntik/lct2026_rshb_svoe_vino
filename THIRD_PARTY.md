# Сторонние компоненты и права третьих лиц

Наша лицензия ([`LICENSE`](LICENSE)) — проприетарная и покрывает **только код и документацию,
написанные командой «Свой Сомелье»**. Всё перечисленное ниже нам не принадлежит и используется
на условиях собственных правообладателей. Этот файл — честный разбор: что именно чужое, где оно
в дереве, под какой лицензией и что из этого ограничивает использование сильнее остального.

Составлен 27.09.2026 архитектором: по манифестам зависимостей (`pyproject.toml`,
`package.json`), по метаданным установленных пакетов (`*.dist-info/METADATA`), по реестру
моделей установленного `fastembed` и по карточкам моделей в
[`docs/architecture/models-and-algorithms.md`](docs/architecture/models-and-algorithms.md)
и [`docs/architecture/lld-rag-chat-pairing.md`](docs/architecture/lld-rag-chat-pairing.md).
Юридического ревью не было — это инженерная сводка.

## 0. Короткий ответ: что ограничивает сильнее всего

| Компонент | Лицензия | Где | Что это значит |
|---|---|---|---|
| `jinaai/jina-reranker-v2-base-multilingual` | **CC BY-NC 4.0 — некоммерческая** | стадия реранка RAG-поиска, `packages/rag` | коммерческое использование запрещено правообладателем модели. Стадия отключается пустым `RAG_RERANKER_MODEL`, поиск при этом продолжает работать на RRF-порядке (`rag/rerank.py`) |
| Веса **YOLO11n** (Ultralytics) | **AGPL-3.0** либо коммерческая лицензия Ultralytics | эксперимент `apps/shelf-finder`, браузерный `baseline`-режим; веса скачивает `scripts/prepare.py --weights yolo11n.pt` | копилефт. Весов и экспортированного ONNX в репозитории нет; в продуктовый путь сканера (`packages/cv` + `apps/api`) YOLO не входит |
| Бинарные колёса **pillow-heif** | код BSD-3-Clause, **колёса GPLv2** (внутри libheif/libde265 LGPLv3, x265 GPLv2) | `packages/cv` (декодирование HEIC с iPhone) | ограничение возникает при распространении собранного образа, а не при оценке кода. Источник: `pillow_heif-1.8.0.dist-info/licenses/LICENSES_bundled.txt` |
| Данные каталога «Своё Вино» | права портала и РСХБ | `pipeline/catalog/` (3414 файлов) и производные индексы | не наши данные; нашей лицензией не передаются |
| Материалы кейса, живые фото, `eval/` | права организаторов ЛЦТ 2026 и кейсодержателя | `eval/`, `case-data/` (вне git) | не наши материалы; `eval/` приведён как есть, без правок |

Остальное — Apache-2.0 / MIT / BSD-3-Clause, то есть совместимо с проприетарным продуктом при
сохранении уведомлений об авторстве.

## 1. Данные, не покрытые нашей лицензией

### 1.1 Платформа «Своё Вино» (vino-svoe.ru) и РСХБ

- `pipeline/catalog/wines/` (1978), `wineries/` (138), `articles/` (1298) — текстовый снимок
  каталога портала, выкачка 25.08.2026 (`pipeline/README.md`). Описания, рейтинги, названия,
  тексты статей и таксономия принадлежат порталу.
- Производные от этих текстов: `pipeline/build/*.jsonl` (5592 чанка), векторный индекс RAG,
  справочники `pipeline/ref/`.
- **Фотографии вин портала в репозитории не хранятся.** Сервис в каждом ответе даёт ссылку на
  страницу-первоисточник и не присваивает авторство описаний.

### 1.2 Организаторы ЛЦТ 2026 и кейсодержатель (РСХБ / «РСХБ.Цифра»)

- ТЗ кейса — в репозитории нет, есть только наша выжимка [`case.md`](case.md) со ссылкой
  на первоисточник.
- Эталонные фотографии (2103 позиции), 100 живых фото с полок и ручная разметка, дамп Strapi —
  вне git, в `case-data/` (правило в `.gitignore`), не распространяются.
- `eval/` (`participant_test.sh`, `queries.tsv`, `checksums.sha256`, `queries/`) — комплект
  проверки, подготовленный кейсодержателем, приведён без правок; права у организаторов.
- Наши артефакты, обученные на эталонах кейса, — векторный индекс `case-20260921-d1-b384`,
  `packages/winescan/configs/box_ranker_v1.joblib`, `box_ranker_v2.joblib`, `metric_v1.npz`,
  признаки эталонов в бандлах `apps/shelf-finder` — код и веса наши, но они производны от
  данных кейса, и права на исходные данные остаются за их владельцами.

## 2. Модели боевого пути сканера (`packages/cv`, `apps/api`)

Веса моделей в репозиторий не включены — загружаются из внешних источников при сборке/первом
запуске. Карточки, размеры и замеры — `docs/architecture/models-and-algorithms.md`.

| Модель | Идентификатор | Лицензия | Роль |
|---|---|---|---|
| SigLIP 2 base-384 | `google/siglip2-base-patch16-384` (env `CV_MODEL`) | Apache-2.0 (карточка модели Google на HuggingFace) | визуальный эмбеддинг кадра для ANN-поиска |
| RapidOCR (RapidAI) | пакет `rapidocr>=3.9` | Apache-2.0 (`rapidocr.dist-info`: `License-Expression: Apache-2.0`) | быстрый CPU-OCR этикетки |
| Модели PP-OCRv5 | `ch_PP-OCRv5_det_mobile`, `eslav_PP-OCRv5_rec_mobile` (ONNX внутри пакета RapidOCR) | Apache-2.0 (PaddleOCR / PaddlePaddle) | детектор и распознаватель строк |
| PaddleOCR + PaddlePaddle | `paddleocr>=3.0`, `paddlepaddle>=3.0`; модели `PP-OCRv5_mobile_det`, `eslav_PP-OCRv5_mobile_rec` | Apache-2.0 | OCR-верификатор near-dup (`cv/verify.py`) |
| Мультимодальная модель шлюза | конфигурируется как `qwen3.8-27b` (env `VISION_LLM_MODEL`, `LLM_MODEL`) | линейка Qwen (Alibaba) распространяется под Apache-2.0; **для конкретного чекпойнта на нашем шлюзе отдельно не проверялось** | чтение полей этикетки и сомелье-чат. Веса вне репозитория, исполняются на инфраструктуре шлюза команды |
| Qwen3-VL-4B MLX (локально на Mac) | `mlx-community/Qwen3-VL-4B-Instruct-4bit` (env `VISION_LLM_LOCAL_MODEL`) | Apache-2.0 (Qwen3-VL); MLX-квантизация сообщества наследует лицензию модели | офлайн-чтение этикетки, служба вне репозитория |

## 3. Модели RAG-поиска и чата (`packages/rag`, `packages/llm`)

| Модель | Идентификатор | Лицензия | Роль |
|---|---|---|---|
| Dense-эмбеддер | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, ONNX-экспорт `qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q` (env `RAG_DENSE_MODEL`) | Apache-2.0 (реестр fastembed 0.8.0: `license="apache-2.0"`) | 384-мерные векторы карточек и запросов |
| Реранкер | `jinaai/jina-reranker-v2-base-multilingual` (env `RAG_RERANKER_MODEL`) | **CC BY-NC 4.0 — некоммерческая** (реестр fastembed 0.8.0: `license="cc-by-nc-4.0"`, `onnx_text_cross_encoder.py:60`) | кросс-энкодер реранка top-20; выключается пустым значением env |
| Чат-модель | тот же шлюз, `qwen3.8-27b` | см. §2 | сомелье-чат, гастропары |

## 4. Модели экспериментальных стеков

Границу «продукт против эксперимента» задаёт [`ARCHITECTURE.md`](ARCHITECTURE.md).

### 4.1 `packages/winescan` и архивный снимок `standalone/winescan`

| Компонент | Лицензия |
|---|---|
| OWLv2 `google/owlv2-base-patch16-ensemble` (детектор бутылки) | Apache-2.0 |
| SigLIP 2 `google/siglip2-base-patch16-224` / so400m | Apache-2.0 |
| Qwen3-VL `Qwen/Qwen3-VL-4B-Instruct` | Apache-2.0 |
| ALIKED (локальные признаки, веса через torch.hub) | BSD-3-Clause |
| LightGlue (CVG/ETH Zurich) | Apache-2.0 |
| EasyOCR (JaidedAI) | Apache-2.0 |
| scikit-learn, pandas, scipy | BSD-3-Clause |
| imagehash | BSD-2-Clause |
| pyarrow (Apache Arrow), accelerate | Apache-2.0 |

### 4.2 `apps/shelf-finder` — подробности в [`apps/shelf-finder/THIRD_PARTY.md`](apps/shelf-finder/THIRD_PARTY.md)

Тексты лицензий лежат рядом: `apps/shelf-finder/licenses/`.

| Компонент | Лицензия | Текст |
|---|---|---|
| ALIKED n16 | BSD-3-Clause | `licenses/ALIKED-BSD-3-Clause.txt` |
| LightGlue (CVG/ETH Zurich) | Apache-2.0 | — |
| LightGlue-ONNX (F. M. Sim), ревизия `d12b4ba` | Apache-2.0 | `licenses/LightGlue-ONNX-Apache-2.0.txt` |
| XFeat + LighterGlue (Verlab), ревизия `e92685f` | Apache-2.0 | `licenses/XFeat-Apache-2.0.txt` |
| OpenCV.js (`@techstark/opencv-js`) | Apache-2.0 | в дистрибутиве пакета |
| torchvision MobileNetV3 Small (IMAGENET1K_V1) | BSD-3-Clause (PyTorch) | — |
| **Веса YOLO11n (Ultralytics)** | **AGPL-3.0 либо коммерческая лицензия Ultralytics** | https://www.ultralytics.com/license |

## 5. Библиотеки Python

Лицензии сверены по `*.dist-info/METADATA` установленных версий (27.09.2026).

| Лицензия | Пакеты |
|---|---|
| Apache-2.0 | `transformers`, `sentencepiece`, `paddleocr`, `paddlepaddle`, `rapidocr`, `opencv-python-headless`, `qdrant-client`, `fastembed`, `rank-bm25`, `huggingface-hub`, `python-multipart`, `accelerate`, `pyarrow` |
| MIT | `fastapi`, `pydantic`, `sqlalchemy`, `pyjwt`, `rapidfuzz`, `onnxruntime`, `pyyaml`, `argon2-cffi` |
| MIT-CMU | `pillow` |
| BSD-3-Clause | `uvicorn`, `starlette`, `httpx`, `httpcore`, `sse-starlette`, `pandas`, `scipy`, `scikit-learn`, `pillow-heif` (исходный код) |
| Смешанные (составные пакеты) | `numpy` — BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0; `torch` — Apache-2.0 AND Apache-2.0-WITH-LLVM-exception AND BSD-2/3-Clause AND BSL-1.0 AND MIT |
| **LGPL-3.0-only** | `psycopg` и `psycopg-binary` (драйвер PostgreSQL) — подключается как отдельная библиотека, наш код остаётся проприетарным, но при распространении сборки условия LGPL сохраняются |
| **GPLv2 — только бинарные колёса** | `pillow-heif`: код BSD-3-Clause, колёса объявлены GPLv2 из-за bundled x265 (§0) |
| Unlicense (public domain) | `email-validator` |
| Apache-2.0 (код) + свои условия у данных | `nltk` — загружаемые корпуса и модели имеют собственные лицензии |

Полный транзитивный список воспроизводится командой
`uv pip list --format=json` в соответствующем `.venv` плюс чтение `METADATA`; здесь перечислены
прямые зависимости из `pyproject.toml` каждого пакета.

## 6. Библиотеки фронтенда и мобильной оболочки

| Компонент | Лицензия |
|---|---|
| React, React DOM, React Router | MIT |
| Capacitor (`@capacitor/core`, `ios`, `android`, `cli`, `capacitor-swift-pm`) | MIT |
| Vite, Vitest, MSW, jsdom, Testing Library, Playwright | MIT |
| TypeScript | Apache-2.0 |
| `onnxruntime-web` | MIT |
| `@techstark/opencv-js` | Apache-2.0 |
| Apple Vision (iOS OCR в `apps/shell/plugins/ocr-plugin`) | системный фреймворк Apple, используется по условиям Apple SDK |
| Прототип `design/ui-prototype`, `standalone/winescan/web` (Nuxt, Vue) | MIT; транзитивно приходят `lightningcss` (MPL-2.0), `caniuse-lite` (CC-BY-4.0), BlueOak-1.0.0, CC0-1.0, `node-forge` (BSD-3-Clause OR GPL-2.0) — в клиентский бандл не попадают |

Шрифты: файлов шрифтов в репозитории нет. Веб-клиент подключает Google Fonts по сети —
Cormorant, IBM Plex Sans, IBM Plex Mono (SIL OFL 1.1). Playfair Display упомянут только как имя
семейства в CSS-переменной с системным запасным вариантом; файл шрифта не встраивается.

## 7. Инфраструктура и образы

| Компонент | Лицензия |
|---|---|
| `python:3.12-slim`, `node:22-slim`, `node:20-alpine` | PSF / MIT + лицензии базового дистрибутива (Debian, Alpine) |
| `nginx:alpine`, `nginx:1.27-alpine` | BSD-2-Clause |
| `qdrant/qdrant:v1.11.3` | Apache-2.0 |
| `postgres:16-alpine` | PostgreSQL License |
| GitHub Actions в `.github/workflows/` | лицензии соответствующих actions |

## 8. Оговорки

1. Это инженерная сводка, а не юридическое заключение. Лицензии моделей брались из карточек
   HuggingFace, реестра `fastembed` и README проектов на дату сверки; правообладатель может их
   менять.
2. Требований к лицензиям моделей и реестру отечественного ПО кейс не предъявляет
   (`case.md`), поэтому ограничение CC BY-NC у реранкера не мешает сдаче на хакатоне — но
   помешает коммерческому использованию и названо явно в [`LICENSE`](LICENSE) §4.3.
3. Если компонент указан здесь и в локальном `THIRD_PARTY.md` подпроекта, локальный файл
   подробнее; расхождений на дату сверки нет.
4. Нашли неточность — это баг документации: правьте через архитектора.
