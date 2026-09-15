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
| 5. Мобильная карточка и «Цифровой сомелье» | не начато |
| Docker | не начато |

Ключевые результаты (подробно — docs/RESULTS.md и ARCHITECTURE.md, раздел 4):

| Проверка | Результат |
|---|---|
| Синтетика `synth_v1`, 2103 кадра, настройки сервиса | top-1 0,858, top-5 0,911 |
| То же с идеальным кропом (верхняя граница без ошибок детектора) | top-1 0,942, top-5 0,990 |
| 3 публичных фото через `participant_test.sh` | Массандра угадана; 2 вина вне каталога в `/v1/scan` — «не найдено»; 1,8–2,0 с на фото |

Синтетика оптимистична: в кадре пиксели эталона. Реальных размеченных фото — 3.

## Структура репозитория

```
10. РСХБ.Цифра.pdf          ТЗ кейса
configs/
  photo_overrides.csv       ручные решения «вино -> файл эталона» (с причинами)
  eval_public_labels.csv    неофициальная разметка 3 публичных фото
data/                       данные кейса, в git не кладутся
docs/                       DATA.md, RESULTS.md, WORKLOG.md
scripts/extract_dump.sh     распаковка RAR-дампа Strapi
src/winescan/
  config.py, logging_setup.py
  catalog/                  слой 0: CSV, имена Strapi, привязка фото, отчёт
  vision/                   слои 1–2: preprocess, detector (OWLv2), embedder (SigLIP 2), ocr (EasyOCR)
  search/                   слой 3: index, build_index, multi, local_match (SIFT), text_match, rerank
  service/                  слой 4: pipeline (Scanner), app (FastAPI)
  validation/               синтетические «полевые» кадры
  eval/                     метрики, прогоны, подбор весов, IoU детектора, сводная таблица
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

# индексы эталонов -> artifacts/index/ (~3 мин и ~2,5 мин)
CUDA_VISIBLE_DEVICES=2 python -m winescan.search.build_index --model google/siglip2-so400m-patch14-384 --batch-size 16
CUDA_VISIBLE_DEVICES=2 python -m winescan.search.build_index --model google/siglip2-so400m-patch14-384 --view label --batch-size 16
```

Оценка:

```bash
# прогон: --crop gt|detector|none, --ocr сохраняет текст этикетки
CUDA_VISIBLE_DEVICES=2 python -m winescan.eval.run --split synth_v1 \
    --index siglip2-so400m-patch14-384,siglip2-so400m-patch14-384__label --crop detector --ocr --batch-size 16
python -m winescan.eval.local_rerank_eval <папка прогона> --split synth_v1   # SIFT, подбор веса (CPU)
python -m winescan.eval.rerank_sweep <папка прогона>                         # текст OCR, подбор веса
CUDA_VISIBLE_DEVICES=2 python -m winescan.eval.detector_eval --split synth_v1 --limit 400   # IoU детектора
python -m winescan.eval.report                                               # -> docs/RESULTS.md
```

## Сервис

```bash
CUDA_VISIBLE_DEVICES=2 .venv/bin/uvicorn winescan.service.app:app --host 0.0.0.0 --port 8080
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
| `POST /v1/scan` (multipart `image`) | статус found / not_found, карточка, уверенность, top-5 со слагаемыми скора, рамка, тайминги |
| `GET /v1/wines/{slug}`, `GET /v1/wines/{slug}/image` | карточка и эталонное фото |
| `GET /health` | готовность |

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
| `WINESCAN_INDEXES` | `siglip2-so400m-patch14-384,siglip2-so400m-patch14-384__label` | индексы сервиса |
| `WINESCAN_INDEX_WEIGHTS` | `0.5,0.5` | веса индексов |
| `WINESCAN_DEVICE` | `cuda`, если доступна, иначе `cpu` | устройство моделей |
| `WINESCAN_USE_DETECTOR` | `1` | `0` — искать по всему кадру |
| `WINESCAN_LOCAL_TOP`, `WINESCAN_LOCAL_WEIGHT` | `5`, `0.15` | SIFT-переранжирование (вес `0` — выключено) |
| `WINESCAN_USE_OCR`, `WINESCAN_TEXT_WEIGHT` | `1`, `0.02` | текстовое переранжирование |
| `WINESCAN_MIN_VISUAL_SCORE` | `0.74` | ниже — `/v1/scan` отвечает «не найдено» |
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
- Интерфейс, «Цифровой сомелье» и Docker пока не сделаны.
- Предоставленный LLM-шлюз не использовался: запрос к нему заблокирован настройками прав.
