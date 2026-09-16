# WineScan — сканер российских вин по фото этикетки

Решение кейса РСХБ «Сканер российских вин с описанием на платформе „Своё вино“»
(«Лидеры цифровой трансформации 2026»). Пользователь фотографирует бутылку у полки,
сервис возвращает **одну** карточку вина из каталога «Своё вино». Техническое задание —
[`10. РСХБ.Цифра.pdf`](10.%20РСХБ.Цифра.pdf).

| Документ | О чём |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | пайплайн, границы слоёв и пакетов, контракты, решения, риски |
| [docs/RESULTS.md](docs/RESULTS.md) | таблица всех прогонов (генерируется `winescan.eval.report`) |
| [docs/DATA.md](docs/DATA.md) | что лежит в данных кейса, особенности и ошибки данных |
| [docs/WORKLOG.md](docs/WORKLOG.md) | журнал работ: что сделано, что получилось и что нет |

## Статус

| Слой (см. ARCHITECTURE.md) | Состояние |
|---|---|
| 0. Данные: распаковка дампа, чистый каталог, привязка эталонных фото | готово |
| 1. Нормализация: детекция упаковки (OWLv2), кроп, белый квадрат, вид «этикетка» | готово |
| 2. Признаки: SigLIP 2 so400m, EasyOCR, SIFT | готово |
| 3. Поиск: сумма индексов, SIFT- и текстовое переранжирование, решение «не найдено» | готово |
| 4. API: `POST /v1/eval/predict`, `POST /v1/scan`, карточки | готово, проверено `participant_test.sh` |
| 5. Функция после поиска: аналоги и «Цифровой сомелье» (бэкенд, без LLM) | готово |
| 5. Мобильный интерфейс (Nuxt 4, `web/`) | готово: сборка и typecheck проходят; на реальном телефоне не проверялся |
| Обучаемый выбор рамки, обученное слияние и отказ по его логиту | готово, измерено на синтетике |
| VLM (Qwen3-VL-4B) в полосе сомнения | реализовано, выключено: 10–14 с на фото |
| Docker, Makefile | готово, образ собирается, smoke-тест пройден |

Ключевые результаты (подробно — docs/RESULTS.md и ARCHITECTURE.md, раздел 4):

| Проверка | Результат |
|---|---|
| Сквозной прогон сервиса с умолчаниями, отложенные запросы `synth_v2` (533, повороты, тесные соседи) | top-1 0,702, top-5 0,780 |
| То же, `synth_v1` (650) | top-1 0,869, top-5 0,934 |
| Поиск с идеальной рамкой (верхняя граница без ошибок детектора), `synth_v2` / `synth_v1` | top-1 0,768 / 0,911 |
| Задержка сервиса на RTX 3090 (150 кадров, свободная GPU) | p50 1,0 с, p95 1,6 с |
| 3 публичных фото через `participant_test.sh` | Массандра угадана; 2 вина вне каталога в `/v1/scan` — «не найдено»; 2,0–2,1 с на фото по замеру скрипта |

Синтетика оптимистична: в кадре пиксели эталона. Реальных размеченных фото — 3.

## Структура репозитория

```
10. РСХБ.Цифра.pdf          ТЗ кейса
configs/
  photo_overrides.csv       ручные решения «вино -> файл эталона» (с причинами)
  eval_public_labels.csv    неофициальная разметка 3 публичных фото
  box_ranker_v1.joblib      обученный выбор рамки
  fusion_*.json             обученное слияние признаков и порог отказа
data/                       данные кейса, в git не кладутся
docs/                       DATA.md, RESULTS.md, WORKLOG.md, RESEARCH.md, PLAN.md
docker/, docker-compose.yml образ сервиса (GPU и CPU)
scripts/extract_dump.sh     распаковка RAR-дампа Strapi
src/winescan/
  config.py, logging_setup.py
  catalog/                  слой 0: CSV, имена Strapi, привязка фото, отчёт
  vision/                   слои 1–2: preprocess, detector (OWLv2), embedder (SigLIP 2), ocr (EasyOCR),
                            vlm (Qwen3-VL), cylinder (поворот цилиндра)
  search/                   слой 3: index, build_index, multi, box_selection, box_ranker, local_match и
                            local_features (SIFT), deep_match (ALIKED + LightGlue), verify, text_match,
                            fields, fusion, rerank
  service/                  слой 4: pipeline (Scanner), app (FastAPI)
  product/                  слой 5: analogs, sommelier
  validation/               синтетические «полевые» кадры (пресеты v1, v2)
  eval/                     метрики, прогоны, кэши запросов, офлайн-эксперименты, обучение выбора рамки
                            и слияния, IoU детектора, сводная таблица
web/                        мобильный интерфейс на Nuxt 4 (свой README)
tests/                      pytest (без GPU и данных кейса)
artifacts/                  результаты сборок и прогонов, в git не кладутся
```

## Требования

- Linux (проверено на Ubuntu 24.04), Python 3.12.
- `unrar` с поддержкой RAR5: `sudo apt-get install unrar` (multiverse). Установленный в Ubuntu
  `7z` без плагина `7zip-rar` не подходит: пишет `Unsupported Method` и оставляет пустые файлы.
- GPU NVIDIA желателен: SigLIP 2 so400m + OWLv2 + EasyOCR занимают ~6–9 ГБ видеопамяти
  (проверено на RTX 3090). Без GPU всё работает на CPU, но медленнее.
- Диск: ~2,5 ГБ распакованный дамп, ~7 ГБ `.venv`, ~5 ГБ кэш моделей HuggingFace,
  ~0,4 ГБ синтетическая выборка.
- Доступ к huggingface.co и github.com при первом запуске (загрузка моделей).
- Необязательно, для локальных признаков ALIKED + LightGlue (эксперимент PLAN 2.2):
  `pip install "lightglue @ git+https://github.com/cvg/LightGlue.git" kornia "kornia-rs==0.1.7"`.
  Версия `kornia-rs` закреплена намеренно: свежие сборки этого расширения требуют инструкций
  AVX2, которых нет у Xeon E5-2670 v2 на нашем сервере, и любой импорт падает с
  `Illegal instruction`.

## Установка

```bash
python3 -m venv .venv
.venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
.venv/bin/pip install -e ".[ml,dev]"
cp .env.example .env    # по желанию; .env в git не попадает
```

Для слоя 0 хватает `pip install -e ".[dev]"` без torch.

## Данные

Положить выданные файлы в `data/`: `strapi_output0709.csv`, `prod-svoe-vino-strapi.part{1,2,3}.rar`,
`eval.zip`. Распаковать:

```bash
scripts/extract_dump.sh      # ~10 с, 15 803 файла; падает, если остались пустые файлы
unzip -oq data/eval.zip -x '__MACOSX/*' -d data/eval
(cd data/eval && sha256sum -c checksums.sha256)
```

## Полный пайплайн

Команды выполняются из корня репозитория, `python` = `.venv/bin/python`. Время указано для
40 CPU и RTX 3090 на свободной машине; на загруженном общем сервере прогоны оценки дольше.

```bash
# слой 0: чистый каталог -> artifacts/catalog/ (~3 мин; --no-review ~1 мин)
python -m winescan.catalog.build

# синтетическая валидация -> artifacts/validation/synth_v1/ (~4 мин)
python -m winescan.validation.build_synth --name synth_v1 --seed 1

# индексы эталонов сервиса -> artifacts/index/: 5 поворотов эталона (~12 и ~9 мин; make index GPU=3)
CUDA_VISIBLE_DEVICES=3 python -m winescan.search.build_index --model google/siglip2-so400m-patch14-384 --yaws=-30,-15,0,15,30 --batch-size 32
CUDA_VISIBLE_DEVICES=3 python -m winescan.search.build_index --model google/siglip2-so400m-patch14-384 --view label --yaws=-30,-15,0,15,30 --batch-size 32
# фронтальные индексы — базовая линия и кэши запросов (~3 и ~2,5 мин; make index-frontal GPU=3)
CUDA_VISIBLE_DEVICES=3 python -m winescan.search.build_index --model google/siglip2-so400m-patch14-384 --batch-size 16
CUDA_VISIBLE_DEVICES=3 python -m winescan.search.build_index --model google/siglip2-so400m-patch14-384 --view label --batch-size 16
# SIFT-признаки и вырезки эталонов для проверки кандидатов (CPU)
python -m winescan.search.local_features
```

Модели выбора рамки и слияния (уже лежат в `configs/`; пересобрать — так):

```bash
python -m winescan.validation.build_synth --name synth_v2 --preset v2 --seed 2
CUDA_VISIBLE_DEVICES=3 python -m winescan.eval.query_cache --split synth_v1 --boxes 3   # ~50 мин на сплит
CUDA_VISIBLE_DEVICES=3 python -m winescan.eval.query_cache --split synth_v2 --boxes 3
Y=siglip2-so400m-patch14-384__yaw-30_-15_0_15_30,siglip2-so400m-patch14-384__label__yaw-30_-15_0_15_30
python -m winescan.eval.offline index-compare --cache synth_v2 --index-sets \
    siglip2-so400m-patch14-384,siglip2-so400m-patch14-384__label $Y          # галереи за секунды
python -m winescan.eval.train_box_ranker --indexes $Y --out configs/box_ranker_v2.joblib
for c in synth_v1 synth_v2; do python -m winescan.eval.candidates --cache $c --indexes $Y \
    --box-ranker configs/box_ranker_v2.joblib --name candidates_yaw_ranker2; done   # ~5 мин на сплит
python -m winescan.eval.train_fusion --cache synth_v1,synth_v2 --table candidates_yaw_ranker2 --out configs/fusion_v2.json
```

Кэши `query_cache` строятся с индексами из конфига сервиса. Кэши в WORKLOG построены на
фронтальных индексах; `--indexes` пересчитывает скоры по другой галерее, эмбеддинги запросов
при этом не пересчитываются.

Оценка:

```bash
# прогон: --crop gt|detector|none, --ocr сохраняет текст этикетки
CUDA_VISIBLE_DEVICES=3 python -m winescan.eval.run --split synth_v1 \
    --index siglip2-so400m-patch14-384,siglip2-so400m-patch14-384__label --crop detector --ocr --batch-size 16
python -m winescan.eval.local_rerank_eval <папка прогона> --split synth_v1   # SIFT, подбор веса (CPU)
python -m winescan.eval.rerank_sweep <папка прогона>                         # текст OCR, подбор веса
CUDA_VISIBLE_DEVICES=3 python -m winescan.eval.detector_eval --split synth_v1 --limit 400   # IoU детектора
# сквозной прогон сервисного Scanner (конфиг из WINESCAN_*): решение «не найдено», p50/p95;
# --holdout — только запросы, которые не видели при обучении выбора рамки и слияния
CUDA_VISIBLE_DEVICES=3 python -m winescan.eval.scanner_eval --split synth_v2 --tag default --holdout
python -m winescan.eval.report --json artifacts/eval/summary.json            # -> docs/RESULTS.md и сводка для страницы метрик
```

## Сервис

```bash
CUDA_VISIBLE_DEVICES=3 .venv/bin/uvicorn winescan.service.app:app --host 0.0.0.0 --port 8080
```

Старт с загрузкой и прогревом моделей — около 40 с (готовность — `GET /health`). Остановка —
Ctrl+C; если сервис запущен в фоне, завершите процесс, который слушает порт (у `pkill -f`
шаблон совпадает и с запускающей оболочкой):

```bash
kill "$(ss -ltnp | grep ':8080 ' | grep -o 'pid=[0-9]*' | cut -d= -f2)"
```

Проверка скриптом кейсодержателя и ручной запрос:

```bash
mkdir -p artifacts/eval/participant_public
(cd data/eval && ./participant_test.sh --images-dir ./queries --manifest ./queries.tsv \
    --endpoint http://127.0.0.1:8080/v1/eval/predict \
    --output "$PWD/../../artifacts/eval/participant_public/predictions.jsonl")
curl -F image=@data/eval/queries/02eef911.webp http://127.0.0.1:8080/v1/scan
```

| Эндпоинт | Ответ |
|---|---|
| `POST /v1/eval/predict` (multipart `image`) | `{"slug": "…"}` — всегда лучший кандидат |
| `POST /v1/scan` (multipart `image`) | статус found / not_found, карточка, уверенность (полоса high / medium / low), top-5 со слагаемыми скора, рамка, тайминги |
| `GET /v1/wines/{slug}`, `GET /v1/wines/{slug}/image` | карточка и эталонное фото |
| `GET /v1/wines/{slug}/analogs?limit=6` | аналоги других виноделен с объяснением по совпавшим полям |
| `GET /v1/sommelier/questions`, `POST /v1/sommelier/suggest` | «Цифровой сомелье»: вопросы-кнопки и 3 вина из каталога с объяснением |
| `GET /v1/metrics` | сводка прогонов для страницы метрик (файл `artifacts/eval/summary.json` готовит `make report`) |
| `GET /health` | готовность |

## Интерфейс

Мобильный интерфейс в `web/` (Nuxt 4, Node.js 22+) ходит в сервис через свой прокси `/api/**`.
Подробности, экраны и режим демо-данных без Python — [web/README.md](web/README.md).

```bash
cd web && npm ci
NUXT_API_BASE=http://127.0.0.1:8080 npm run dev     # http://localhost:3000
NUXT_PUBLIC_MOCK=1 npm run dev                      # без сервиса, на фикстурах
```

## Makefile

`make help` — список целей. Основные: `make install`, `make data`, `make artifacts GPU=3`
(каталог, индексы с поворотами, SIFT-признаки и вырезки эталонов), `make synth`,
`make cache GPU=3`, `make eval GPU=3` (поиск), `make scanner GPU=3` (сквозной прогон сервиса
с решением «не найдено» и задержками), `make serve GPU=3`, `make participant`, `make test`.

## Docker

Нужен NVIDIA Container Toolkit (для CPU — профиль `cpu`). Данные кейса и артефакты монтируются
томами, веса моделей кэшируются в томе `hf-cache`.

```bash
docker compose build api web
docker compose run --rm api make PY=python catalog index features GPU=0   # артефакты внутри контейнера
docker compose up api web                                                  # GPU; интерфейс на :3000
NUXT_API_BASE=http://api-cpu:8080 docker compose --profile cpu up api-cpu web   # CPU
NUXT_PUBLIC_MOCK=1 docker compose up web                                   # интерфейс на демо-данных
```

## Тесты

```bash
.venv/bin/python -m pytest
```

Тесты не требуют GPU, моделей и данных кейса: синтетические CSV, картинки и поддельный сканер.

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `WINESCAN_DATA_DIR` | `data` | корень данных кейса |
| `WINESCAN_CATALOG_CSV` | `$WINESCAN_DATA_DIR/strapi_output0709.csv` | CSV-выгрузка каталога |
| `WINESCAN_UPLOADS_DIR` | `$WINESCAN_DATA_DIR/raw/prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads` | файлы Strapi |
| `WINESCAN_ARTIFACTS_DIR` | `artifacts` | результаты сборок и прогонов |
| `WINESCAN_PHOTO_OVERRIDES` | `configs/photo_overrides.csv` | ручные решения по фото |
| `WINESCAN_INDEXES` | `siglip2-so400m-patch14-384__yaw-30_-15_0_15_30,siglip2-so400m-patch14-384__label__yaw-30_-15_0_15_30` | индексы сервиса: вся упаковка и этикетка, по 5 поворотов эталона |
| `WINESCAN_INDEX_WEIGHTS` | `0.5,0.5` | веса индексов |
| `WINESCAN_DEVICE` | `cuda`, если доступна, иначе `cpu` | устройство моделей |
| `WINESCAN_USE_DETECTOR` | `1` | `0` — искать по всему кадру |
| `WINESCAN_LOCAL_TOP`, `WINESCAN_LOCAL_WEIGHT` | `5`, `0.15` | SIFT-переранжирование (вес `0` — выключено) |
| `WINESCAN_USE_OCR`, `WINESCAN_TEXT_WEIGHT` | `1`, `0.02` | текстовое переранжирование |
| `WINESCAN_MIN_VISUAL_SCORE` | `0.74` | ниже — `/v1/scan` отвечает «не найдено» |
| `WINESCAN_BOX_CANDIDATES`, `WINESCAN_BOX_RULE` | `3`, `{"prior": 1, "top1": 0, "margin": 0}` | сколько рамок детектора рассматривать; правило выбора (JSON), если нет обученного |
| `WINESCAN_BOX_RANKER` | `configs/box_ranker_v2.joblib` | обученный выбор рамки (обучен на скорах галереи с поворотами); `0` — выбор по правилу |
| `WINESCAN_FUSION` | `configs/fusion_v2.json` | обученное слияние: признаки проверки кандидатов и логит в деталях ответа; `0` — прежний путь (SIFT на исходном кропе) |
| `WINESCAN_FUSION_RANK` | `0` | `0` — гибрид: порядок по ручным весам SIFT и OCR на inliers проверки (лучше на `synth_v2`); `1` — порядок по логиту слияния |
| `WINESCAN_FUSION_REJECT` | `0` | `1` — «не найдено» и при логите лучшего кандидата ниже `reject_logit` модели (слабый сигнал, см. WORKLOG) |
| `WINESCAN_USE_VLM`, `WINESCAN_VLM_MARGIN` | `0`, `1.0` | поля этикетки Qwen3-VL-4B, если отрыв лучшего кандидата меньше порога (нужно слияние) |
| `WINESCAN_MIN_MARGIN` | не задан | минимальный отрыв top-1 от top-2 |
| `CUDA_VISIBLE_DEVICES` | — | на общем сервере GPU 0 и 1 заняты |
| `WINESCAN_LLM_BASE_URL`, `WINESCAN_LLM_API_KEY` | — | зарезервировано для слоя 5, пока не используется |

Относительные пути считаются от корня репозитория.

## Ручные решения по фото

`configs/photo_overrides.csv`, колонки `slug,image_file,reason`. Пустой `image_file` означает
«у вина нет подходящего эталона». Сборка падает, если slug или файл не существуют.

## Ограничения

- Почти все метрики — на синтетике из эталонов: она оптимистична. Реальных размеченных фото 3.
- Дампа базы Strapi нет: связь «вино → файл» восстановлена эвристиками и 13 ручными решениями.
- 59 вин делят байт-в-байт одинаковые эталоны, 212 групп вин одной винодельни различаются
  только цветом, сладостью или годом; дубли карточек не различит никакой метод.
- SIFT не видит цвета, OCR на стилизованных этикетках шумный.
- Задержки измерены на общем сервере под нагрузкой (load average до 800).
- «Цифровой сомелье» работает на правилах без LLM; интерфейс не проверялся на реальном телефоне.
- Модели выбора рамки и слияния обучены только на синтетике; порог отказа выбран осторожным,
  потому что порог «максимум точности» с синтетики отклонял верный ответ на реальном фото.
- Предоставленный LLM-шлюз не использовался: запрос к нему заблокирован настройками прав.
