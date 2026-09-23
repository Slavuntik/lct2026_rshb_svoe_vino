# backend — Swagger/OpenAPI под `/v1` для стенда и жюри (22.09)

Задача тимлида: nginx стенда (`infra/ams3/nginx-somelye.conf`) проксирует в API только `/v1/*`, дефолтные `/docs`/`/openapi.json` FastAPI снаружи падали в SPA-фоллбэк.

## Что сделано (`apps/api/app/main.py`)

- `docs_url="/v1/docs"`, `redoc_url="/v1/redoc"`, `openapi_url="/v1/openapi.json"` —
  старые пути теперь 404 у самого приложения, не только "перехвачены" снаружи nginx.
- `title="Свой Сомелье API"`, `version="0.3.3"` (= `contracts/openapi.yaml`, ручная
  синхронизация, отмечено комментарием), `description` — как получить гостевой токен
  (`POST /v1/auth/guest`), кнопка Authorize (вставлять только сам токен, без слова
  "Bearer"), разница `/v1/eval/predict` (без токена, плоский ответ скрипту
  кейсодержателя) vs `/v1/scan/photo` (полный), ссылки на `contracts/image-scan.md` и
  `contracts/post-scan.md`.
- `tags_metadata` — 14 тегов, не 7 из брифа: реально на роутерах стоят health/auth/
  consents/scan/eval/wines/chat/analogs/taste/profile/events/waitlist/metrics/
  case-thumbs (сверено `grep tags= app/routers/*.py`); "sommelier" из брифа — это тег
  `chat` (роутеры не трогал). Подписаны описанием все 14.
- **Security-схемы Bearer не было вообще**, не просто "без описания" — auth разобран
  вручную по заголовку `Authorization` (`app/security.py`), в проекте ни одной
  `fastapi.security`-зависимости, поэтому Authorize не рисовался. Добавил
  `custom_openapi()`: описательная схема `BearerAuth` (http/bearer/JWT + текст) поверх
  стандартного `get_openapi()`, `security` выставлен глобально. Рантайм-проверку токена
  не менял (осталась в `security.py`) — эффект только на `/v1/docs`.

## Проверка CDN (пункт брифа)

Swagger UI/ReDoc берут дефолтные ассеты FastAPI с `cdn.jsdelivr.net`, не самохостил.
Поднял живьём (`uvicorn` на :8771) и открыл в браузере: `/v1/docs` и `/v1/redoc`
рендерятся полностью (стили, рабочий Authorize-диалог, 0 ошибок в консоли) — CDN
грузится. **Анти-CDN политика в проекте есть, но не на стенде**: `infra/nginx.conf`
(прод/стейджинг за Cloudflare, чужая зона) несёт CSP `script-src 'self'; style-src …
https://fonts.googleapis.com` — заблокирует jsdelivr, если `/v1/docs` когда-нибудь
откроют через этот контур. У `infra/ams3/nginx-somelye.conf` (стенд этой задачи) CSP
нет вообще — там всё грузится. Понадобится Swagger и на prod — самохостить ассеты или
добавить jsdelivr в CSP (решение architect/devops).

## Тесты

Новый `apps/api/tests/test_docs.py` (5 тестов; ожидаемые пути собираются из модулей
роутеров, не из схемы — не тавтология): `/v1/docs` → 200 text/html, `/v1/redoc` → 200,
`/v1/openapi.json` → 200 + все зарегистрированные маршруты + описанная `BearerAuth`,
старые `/docs`/`/redoc`/`/openapi.json` → 404.
`cd apps/api && .venv/bin/pytest -q` — **365 passed, 11 skipped** (было 360/11 до
правки, регрессий нет; skip — integration-тесты без `RUN_*_INTEGRATION`).

## README.md

Раздел «Быстрый старт» дополнен адресом Swagger на стенде — **http://89.110.72.101/v1/docs**
(+ локальный адрес, ReDoc, `openapi.json`). Правка корня — по прямому пункту тимлида, вне обычной зоны backend.

## Риски / предложения

- Глобальный `security` вешает замок в Swagger на ВСЕ операции, включая честно
  публичные (`/v1/eval/predict`, `/v1/waitlist`, `/v1/healthz`, `/v1/metrics/scan`,
  `/v1/case-thumbs/*`) — косметика документации, на рантайм-доступ не влияет. Точечная
  зачистка (`security: []` на операциях) потребовала бы правок самих роутеров — вне
  main.py, можно отдельным тикетом.
- `API_VERSION` в `main.py` — ручная копия версии `contracts/openapi.yaml`, молча разойдётся при следующей правке. Предложение architect: не забыть main.py в чек-лист бампа версии.
