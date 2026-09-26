# devops: проброс апстрима витрины Потапа — конфиг готов, включение ждёт адрес и root (26.09)

Задача тимлида 26.09. Адреса сервиса `apps/shelf-finder/server` (:8086) пока нет — Вячеслав уточняет.

## Сделано (в git)

- `infra/ams3/nginx-somelye.conf`: locations `/v1/shelf/` и `/shelf-ui/` читают `$shelf_upstream`;
  пустое значение → `return 404;` ДО `proxy_pass` (честно, не `try_files`-фолбэк на наш SPA).
  `/v1/shelf/` длиннее общего `/v1/` — nginx матчит самый длинный префикс НЕЗАВИСИМО от порядка
  блоков, общий `/v1/` → `apps/api` не задет гарантированно. `/shelf-ui/` — `rewrite ... break`
  отбрасывает префикс. Лимиты уже внутри: `client_max_body_size 20m` (лимит сервиса), таймауты 60с
  (сервис просит ≥60с при клиентских 45с).
- `infra/ams3/shelf-upstream.conf` (новый): `map $host $shelf_upstream { default ""; }` для
  `/etc/nginx/conf.d/`. `map`, не `set`: `conf.d` этой коробки подключается ВНУТРИ `http{}`
  (проверено `grep` на сервере) — `set` там не пройдёт `nginx -t`.
- `infra/ams3/bootstrap.sh`: ставит `shelf-upstream.conf`, только если файла ещё нет на сервере (тот
  же приём, что `somelye.env`) — повторный bootstrap не откатит уже включённый адрес.
- `docs/architecture/operations.md` §10 + `infra/ams3/README.md` («Проброс витрины Потапа»): данные,
  нужные от Михаила (хост/порт/схема/авторизация), команды включения/проверки/отката, разбор риска
  (после включения кто угодно шлёт фото на чужой GPU через наш домен) и минимальная защита — решение
  об усилении сверх лимита тела/таймаутов оставлено Вячеславу.

## Пробел прав — важно тимлиду

`sudo -n -l` на ams3 (26.09): пользователь `somelye`/ключ `ci_do_ams3` может ТОЛЬКО `systemctl
{restart,is-active,status} somelye-api`; `/etc/nginx/*` — `root:root`, ни записи, ни `nginx -t`, ни
`reload` в этот аккаунт не входят. **Включение (правка `shelf-upstream.conf` + `nginx -t` + reload)
требует root** — сам не применял и не пытался обойти (правка sudoers — вне мандата devops, это
изменение прав на боевом VPN-боксе). Нужно решение Вячеслава: либо он выполняет готовые 3 команды
сам (README §«Проброс витрины Потапа»/operations.md §10.3), либо расширяет sudoers на `nginx -t` +
`systemctl reload nginx` для самообслуживания devops в будущем.

## Проверено

- **Baseline на стенде** (текущий, неизменный nginx, через публичный `:80`): `/v1/healthz` 200
  `warm:true`; `/v1/metrics/scan` 200 (`f1_top1=0.968`); веб `/` 200; `/v1/eval/predict` на 1 фото из
  `/opt/somelye/cpulab/real-photos/` (без выхода фото за пределы сервера) — 200 `{"slug":...}` за
  3.5с. `/v1/shelf/health` сегодня — `404 application/json`; `/shelf-ui/` — `200 text/html`
  (SPA-фолбэк) — оба ожидаемо, до включения.
- **`nginx -t` кандидат-конфига реальным бинарём 1.28.3 с сервера**: изолированная копия
  `nginx.conf` (pid/логи/`conf.d`/`sites-enabled` → scratch в `/tmp`, без root, боевые файлы не
  тронуты) с моими `nginx-somelye.conf`+`shelf-upstream.conf` — `test is successful` и в выключенном,
  и в имитации включённого (`https://127.0.0.1:8086`) состояния.
- `infra/scripts/validate.sh`: 33 OK / 2 FAIL — оба FAIL про `compose.prod/staging.yml` (нужны
  `POSTGRES_*` в `.env` для биз-линии Contabo), не мои файлы, до этой задачи уже красные.
- `bash -n infra/ams3/bootstrap.sh` — чисто.

## Как воспроизвести

`infra/ams3/README.md` → «Проброс витрины Потапа»: данные от Потапа, 3 команды включения, проверка
(`curl .../v1/shelf/health` ожидание `200 application/json`), откат (`default "";` + reload).

## Риски / вопросы тимлиду

1. Активация ждёт (а) адрес от Потапа, (б) root на ams3 — сам не имею и не должен получать явочным
   порядком.
2. Реальный адрес сервиса НЕ коммитить в `shelf-upstream.conf` — репозиторий публичный, ставить
   только на сервере (тот же принцип, что `VISION_LLM_URL`).
3. Решение по усилению защиты (токен/`allow`-`deny`/`limit_req`) сверх уже заложенного лимита тела и
   таймаутов — за Вячеславом (`operations.md` §10.6), не блокирует само включение.
