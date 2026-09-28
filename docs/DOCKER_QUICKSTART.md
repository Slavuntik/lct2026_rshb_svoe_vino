# Vinchik: quick-start в Docker

Из корня репозитория запускаются основной веб-клиент `apps/web`, API и, в полном
режиме, отдельный сервис витрин. GPU на Docker-хосте не нужен: локальные модели
работают на CPU, Qwen вызывается через HTTP(S) API LiteLLM.

## Требования

Docker Engine с BuildKit или Docker Desktop с Compose v2.24+ (команды `docker version`,
`docker compose version`). Для полного режима ориентируйтесь на Linux x86_64,
4 CPU, 16 ГБ RAM и 20 ГБ свободного диска сверх датасета. Это запас для моделей,
сборки и кэшей, не измеренный минимальный предел. Первая сборка требует интернета.
При первом полном запуске RapidOCR также скачивает ONNX-веса в постоянный
том `api_cache` (`/cache/rapidocr`); последующие запуски используют этот кэш.
Для полностью офлайн-запуска заранее заполните его моделями RapidOCR той же версии.
Образы явно используют `linux/amd64`. На ARM/Mac потребуется эмуляция amd64;
такой запуск не проверен и будет медленнее. Node 24.20.0 в стадии сборки web
берётся из официального архива nodejs.org с проверкой закреплённой SHA256.

## 1. Быстрая проверка интерфейса и API

```bash
cp .env.docker.example .env.docker
openssl rand -hex 32
# Вставьте результат в JWT_SECRET в .env.docker.
chmod 600 .env.docker
docker compose --env-file .env.docker up -d --build --wait --wait-timeout 600
```

Откройте **http://localhost:8080**, Swagger: http://localhost:8080/v1/docs.
Этот режим использует заглушки каталога, распознавания и LLM; он проверяет
интерфейс, авторизацию и API, но не распознаёт реальные фотографии. Витрина
становится работоспособной после подключения полного режима ниже.

Не подменяйте существующий `.env`: Docker использует отдельный `.env.docker`.
Порт меняется через `DOCKER_PORT`; для доступа с другого устройства установите
`DOCKER_BIND=0.0.0.0`. Доступ к камере через браузер телефона требует HTTPS;
HTTP по IP подходит для загрузки готового файла. TLS настраивается на внешнем
reverse proxy, сертификат в эти образы не включён.

## 2. Подготовьте данные для реального распознавания

Данные организаторов и веса не входят в Git/образ. Нужен согласованный снимок
индексов и моделей, например отдельная копия подготовленных данных нативного
стенда. **Не подключайте живые embedded Qdrant-каталоги одновременно к двум API.**
Снимок индексов делайте при остановленном владельце индекса либо из готового
офлайн-экспорта. Пользовательскую БД/секреты/диагностические фото копировать не нужно.

```text
/absolute/path/to/vinchik-data/
  case/              case_catalog.json, strapi_output0709.csv, slug_refs.json,
                     families.json, winery_aliases.json, thumbs/
  cv/                qdrant/ с подготовленным индексом этикеток
  rag/               qdrant/, manifest.json, labels.jsonl, bm25/, payloads/
  fastembed/         подготовленный кэш моделей RAG
  models/hf/         hub/ с SigLIP2, включая файлы, на которые указывают symlink
/absolute/path/to/server-models/
  server-manifest.json, catalog.json, detector.onnx, extractor.pt, matcher.pt,
  retriever.pt, centers.bin, vectors.bin, local-index.json, references/, verification/
```

Названия активных директорий стенда могут быть `cv-d1`, `rag-20260922`:
проверьте действующие `CV_DATA_DIR`/`RAG_DATA_DIR`, а в отдельной копии используйте
имена `cv`/`rag`, показанные выше. `CV_MODEL` обязан совпадать с энкодером индекса.
Инструкции подготовки: [данные и синхронизация](../infra/ams3/README.md),
[модели витрин](../apps/shelf-finder/server/README.md).

Контейнер API работает с UID/GID 10001. На Linux разрешите этому UID чтение данных
и запись в отдельную копию индексов/кэшей, например:

```bash
sudo chown -R 10001:10001 /absolute/path/to/vinchik-data
```

Меняйте владельца только своей Docker-копии. Модели витрины монтируются read-only;
их файлы и родительские каталоги должны быть доступны UID 10001 для чтения/прохода.

В `.env.docker` задайте абсолютные пути:

```dotenv
VINCHIK_DATA_DIR=/absolute/path/to/vinchik-data
SHELF_MODELS_HOST_DIR=/absolute/path/to/server-models
CV_MODEL=google/siglip2-base-patch16-384
```

## 3. Выберите CPU или CPU + LiteLLM и запустите

Полностью локальное распознавание: оставьте `SHELF_PROFILE=baseline`,
`CV_FUSION_TEXT_SOURCE=ocr`, `LLM_PROVIDER=mock`. Поиск и распознавание будут
реальными; разговорный LLM останется заглушкой.

Для Qwen укажите в `.env.docker`:

```dotenv
SHELF_PROFILE=litellm
LLM_PROVIDER=openai
CV_FUSION_TEXT_SOURCE=vlm
LITELLM_URL=https://your-litellm-host/v1
LITELLM_API_KEY=your-key
LITELLM_MODEL=qwen3.8-27b-uncensored
```

Имя модели должно существовать в вашем LiteLLM и поддерживать картинки. Ключ
передаётся только серверным контейнерам; во frontend и Docker build он не попадает.
LiteLLM дополняет локальные модели: всё равно нужны каталог, индексы и веса.

```bash
docker compose --env-file .env.docker \
  -f compose.yaml -f infra/docker/compose.full.yaml \
  up -d --build --wait --wait-timeout 900
```

Первая сборка полного образа значительно дольше быстрого режима. Web запускается
после готовности API, API — после загрузки моделей витрины. Один API worker
обслуживает очередь заданий; не увеличивайте replicas/workers без общего хранилища
заданий и сетевых индексов.

## Проверка и эксплуатация

`python3 infra/docker/smoke.py` проверяет web, API и гостевой вход. В полном режиме
добавьте `--shelf`, а для настоящего задания — `--photo /path/to/shelf.jpg`.
Это создаёт тестового гостя и, при фото, запись в диагностическом архиве.
Workflow `Docker quick-start` повторяет базовую smoke-проверку в GitHub Actions;
реальные модели и фотографии в CI не загружаются.

```bash
curl --fail http://localhost:8080/v1/healthz
curl --fail http://localhost:8080/v1/shelf/health
# Полный режим: warm=true, ready=true, catalogSize > 0.
docker compose --env-file .env.docker -f compose.yaml -f infra/docker/compose.full.yaml ps
docker compose --env-file .env.docker -f compose.yaml -f infra/docker/compose.full.yaml logs --tail=100 api shelf web
```

В интерфейсе: «Найти своё вино на полке» → пожелание → несколько фотографий.
Проверяйте реальные результаты отдельно от healthcheck: готовность сервиса
не гарантирует нахождение каждой бутылки или правильность конкретного SKU.

Для обновления выполните `git pull --ff-only`, затем повторите команду `up`.
Для остановки используйте ту же команду Compose с `down` вместо `up ...`.
В быстром режиме не добавляйте второй `-f`. При обновлении API активные задания
прервутся — обновляйте вне сканирования. API и витрина не публикуют порты наружу;
единственная точка входа — web, запросы витрин проходят авторизацию основного API.

SQLite и архив фото находятся в named volume `app_state`, кэш API — `api_cache`.
`down` сохраняет их; **`down -v` удаляет пользовательскую БД и архив**. Данные по
bind-путям Compose не удаляет. В полном режиме загруженные фото сохраняются в
`/state/shelf-debug` (до 100, очистка старше 7 дней при следующей загрузке);
подробнее [архив диагностики](../infra/native/README.md#диагностические-фотографии).

Если сервис unhealthy: смотрите `logs`. Типовые причины — отсутствующий снимок,
права UID 10001, несовпадение модели/индекса, нескачанный HF-кэш или ошибочные
LiteLLM URL/ключ/модель. Пустой `JWT_SECRET` и несуществующий bind-путь блокируют
запуск вместо создания пустых каталогов. Недоступность LiteLLM после старта
может дать локальные результаты с предупреждением; это не полноценная проверка LLM.

Существующие `infra/compose.prod.yml`/`compose.staging.yml` и systemd-деплой серверов
остаются отдельными вариантами. Этот quick-start автоматически серверы на Docker
не переводит. Механизм готовности соответствует [Compose service_healthy](https://docs.docker.com/compose/how-tos/startup-order/).

## Что проверено

28.09.2026, Linux x86_64, Docker 28.1.1 / Compose 2.35.1:

- Собраны API (базовый и полный), web и Shelf API; оба режима запущены через Compose.
- Проверены nginx, гостевой вход, каталог, правила подбора; в полном режиме все
  три сервиса healthy, витрина загрузила 2103 позиции.
- Через `/v1/shelf/jobs` обработано `photo_2026-09-24_17-34-31.jpg`:
  48 обнаруженных бутылок, 2 подтверждённых вина — Nature Vert и Мюллер-Тургау
  «Высокий Берег». Обе позиции вошли в подбор «белое сухое» из 578 подходящих
  позиций каталога. Запрос без авторизации отклонён с 401.
- `/v1/eval/predict` на эталоне Nature Vert вернул соответствующий slug.
- Контейнер витрины получил ответ LiteLLM через настоящий API. Полный прогон
  фотографии выше выполнен с `SHELF_PROFILE=baseline`, без Qwen.
- 61 тест RapidOCR прошёл; OCR-веса и SQLite сохраняются вне образа.

Это проверка контейнерной интеграции, не новая оценка точности. CPU-обработка
контрольной полки заняла 49–64 с на общей тестовой машине; это не SLA и не замер
выделенного сервера. Docker сам по себе не ускоряет распознавание.
