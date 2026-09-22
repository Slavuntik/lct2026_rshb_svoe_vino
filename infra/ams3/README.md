# Хак-стенд «Свой Сомелье» на exit-ams3

Стенд кейса ЛЦТ живёт на **боевом VPN-боксе** `exit-ams3` (Ubuntu 26.04, 4 vCPU / 7.4 ГБ RAM).
Всё здесь подчинено одному правилу: **VPN важнее стенда**.

## Почему нативно, а не Docker

| Ограничение бокса | Следствие для стенда |
|---|---|
| WireGuard-выход живёт на форвардинге пакетов | Docker при установке ставит политику `FORWARD=DROP` и отрезает WG-клиентов → **Docker не ставим** |
| `:443/tcp` занят Reality (xray), `:8444/udp` — hysteria2, `:59999/udp` — WireGuard | Стенд слушает **только `:80`** (nginx) и `127.0.0.1:8000` (uvicorn) |
| Реплика control-plane VPN на `127.0.0.1:8090` | Не трогаем; порты стенда с ним не пересекаются |
| Общая память с VPN | `MemoryHigh=4500M` / `MemoryMax=5500M` + swap 4 ГБ: при пике OOM бьёт по стенду, не по xray |
| Общий CPU | `CPUWeight=40` (у VPN по умолчанию 100) + `OMP_NUM_THREADS=3` — одно ядро всегда свободно |
| fail2ban режет частые SSH-коннекты | Все скрипты используют мультиплексирование (`ControlMaster`) — одно соединение на всю операцию |

## Грабли CPU-инференса (проверено на этом боксе)

- **oneDNN у paddlepaddle 3.3.1 здесь падает внутри себя** (`ConvertPirAttribute2Runtime-
  Attribute`, связка PIR+oneDNN) — движок не бросает ошибку наружу, а возвращает ПУСТОЙ
  текст: верификатор честно воздерживается, near-dup перестаёт различаться, и это видно
  только по `CV_VERIFY_DEBUG=1`. Лечится `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0` +
  `FLAGS_use_mkldnn=0` в `somelye.env` (ценой скорости: верификация ~2.5-3 с вместо 0.5 с).
- **Модели OCR менять нельзя ради скорости:** мобильный детектор теряет решающую строку
  этикетки, а любое явное имя модели отключает `lang=ru` (кириллица → латинский
  распознаватель → мусор). Оставляем штатные.
- Итог по времени на этом боксе: скан без near-dup ~0.6-1.8 с, с верификацией ~3-4 с.
  Официальный замер SLA кейса — на машине команды (Mac): p50/p95 = 355/635 мс.

**Никогда на этом боксе:** `ufw`/`iptables`/`nftables`, установка Docker, занятие 443/8444/59999,
перезапуск `xray`/`hysteria`/`wg-quick`/`newvpn-*`, пересборка CV-индекса (45 минут CPU — собирать
на Mac и везти готовый).

## Состав

| Файл | Что делает |
|---|---|
| `bootstrap.sh` | Разово от root: пользователь `somelye` (без sudo, кроме рестарта своего сервиса), каталоги, swap, uv + Python 3.12, nginx-сайт, systemd-юнит, `somelye.env` со свежим `JWT_SECRET` |
| `somelye-api.service` | uvicorn под systemd с лимитами памяти и CPU |
| `nginx-somelye.conf` | `:80` → статика SPA + прокси `/v1` на uvicorn (SSE для `/v1/chat` без буферизации, тело до 26 МБ) |
| `somelye.env.example` | Шаблон окружения (реальный файл — только на сервере, 600) |
| `push-release.sh` | Код через `git archive` (выкатывается только закоммиченное) + сборка веба, затем `deploy.sh` |
| `deploy.sh` | На сервере: `uv sync --frozen --extra integration`, рестарт, ожидание `healthz warm:true` |
| `sync-data.sh` | Индексы и модели с Mac (~3.9 ГБ): CV-индекс, RAG-индекс, fastembed, SigLIP2, PaddleOCR, метаданные кейса |

## С нуля

```bash
ssh-keygen -t ed25519 -f ~/.ssh/ci_do_ams3 -N ""            # ключ для CI и выкатов
scp -r infra/ams3 root@<ams3>:/root/ams3-bootstrap          # комплект на сервер
ssh root@<ams3> "bash /root/ams3-bootstrap/bootstrap.sh '$(cat ~/.ssh/ci_do_ams3.pub)'"
bash infra/ams3/sync-data.sh <ams3>                          # данные и модели, ~20 минут
cd apps/web && VITE_API_MODE=real VITE_THEME=portal npm run build && cd ../..
bash infra/ams3/push-release.sh <ams3>                       # код + веб + прогрев
```

## Обновления

- **Код:** тег `hack-v*` или ручной запуск workflow `Deploy hack stand (ams3)`; локально — `push-release.sh`.
- **Индекс после пересборки на Mac:** `sync-data.sh <ams3>` → `ssh somelye@<ams3> 'sudo systemctl restart somelye-api'`.
  Без простоя (blue/green, отдельный каталог + переключение) — CV: «Смена CV-индекса без простоя»
  ниже; RAG: «Смена RAG-индекса без простоя» ниже (`switch-rag`/`rollback-rag`, с 22.09).
- **Откат:** `DEPLOY_REF=<коммит> bash infra/ams3/push-release.sh <ams3>`.

## Секреты GitHub

Три обязательных и один необязательный, тип **Secrets** (не Variables — workflow читает `secrets.*`, переменная из Variables
придёт пустой), уровень **Repository** (не Environment — деплой-джоба не объявляет
`environment:`, секреты окружения ей не видны). Settings → Secrets and variables → Actions →
Secrets → New repository secret:

| Имя | Значение |
|---|---|
| `AMS3_HOST` | `89.110.72.101` |
| `AMS3_SSH_KEY` | приватная половина ключа CI целиком, со строками BEGIN/END: `pbcopy < ~/.ssh/ci_do_ams3` |
| `AMS3_KNOWN_HOSTS` | вывод `ssh-keyscan 89.110.72.101` целиком |
| `GIGACHAT_AUTH_KEY` | *необязательный* — авторизационный ключ GigaChat; есть → «Сомелье» на GigaChat |
| `VISION_LLM_URL` | *необязательный* — адрес OpenAI-совместимого шлюза GPU-сервера (`https://…/v1`); вместе с ключом включает чтение этикетки моделью |
| `VISION_LLM_KEY` | *необязательный* — ключ шлюза. Оба значения едут на сервер через stdin, в репозитории их нет (репозиторий публичный) |

Пользователь `somelye` зашит в `push-release.sh` — отдельный секрет не нужен. Без секретов
workflow не падает, а вежливо пропускает выкат (джоба `guard`).

## Архив сканов (контрольная выборка)

**Временный режим — только на время сбора выборки, в проде его нет.** Выключить: удалить
строку `SCAN_ARCHIVE_DIR` из `/opt/somelye/somelye.env` и `sudo systemctl restart somelye-api`.
Сделать это до того, как ссылку на стенд получат люди вне команды.

Каждое фото, отсканированное через интерфейс, ложится в `/opt/somelye/data/scans/`:
`confident/` — карточка показана, `unsure/` — близкие кандидаты не разведены, `failed/` —
совпадения нет. Рядом с фото — JSON с предсказанием, топ-5 и скорами и пустым полем
`verified_slug` для ручной сверки. Фото перекодировано без метаданных (без GPS).
Скриптовые режимы (`?flat=1`, `/v1/eval/predict`) не архивируются — там приватная выборка
кейсодержателя. Забрать к себе и посмотреть сводку:

```bash
bash infra/ams3/pull-scans.sh 89.110.72.101
```

## Подключение GigaChat (российская LLM для хака)

Сертификат Минцифры уже стоит (`/opt/somelye/certs/russian_trusted_root_ca.pem`, отпечаток сверен
по двум источникам) и применяется только к клиенту GigaChat. API Сбера с этого сервера доступен.

1. developers.sber.ru → GigaChat API → создать проект → скопировать **авторизационный ключ**.
   Физлицо — scope `GIGACHAT_API_PERS` (стоит по умолчанию, есть бесплатный лимит токенов).
2. **Основной путь — секрет GitHub.** Settings → Secrets and variables → Actions → New repository
   secret: `GIGACHAT_AUTH_KEY` = ключ. При следующем выкате (тег `hack-v*` или Run workflow)
   CI передаст его на сервер через stdin и включит `LLM_PROVIDER=gigachat`. Нет секрета — LLM на
   сервере не трогается. Ротация ключа — заменить секрет и перевыкатить.
3. Запасной путь без CI — поставить ключ вручную из буфера обмена:

```bash
pbpaste | ssh -i ~/.ssh/ci_do_ams3 somelye@89.110.72.101 'read -r k; sed -i "/^LLM_PROVIDER=/d; /^GIGACHAT_AUTH_KEY=/d" /opt/somelye/somelye.env; printf "LLM_PROVIDER=gigachat\nGIGACHAT_AUTH_KEY=%s\n" "$k" >> /opt/somelye/somelye.env; sudo /usr/bin/systemctl restart somelye-api'
```

Вернуть заглушку — `LLM_PROVIDER=mock` в том же файле и рестарт.

## Подключение LLM через шлюз (свой Qwen 27B, пока нет ключа GigaChat)

Решение Вячеслава (22.09, `reports/backend-llm-openai.md`): пока нет ключа GigaChat, сомелье-чат
(`/v1/chat`) подключается к GPU-серверу команды через OpenAI-совместимый шлюз (LiteLLM,
`/v1/chat/completions`, Bearer-ключ, модель по умолчанию `qwen3.8-27b`, SSE-стрим шлюз
поддерживает) — драйвер `packages/llm/llm/drivers/openai.py`, `LLM_PROVIDER=openai`. Тот же
драйвер годится для любого другого OpenAI-совместимого шлюза/модели: достаточно сменить
`LLM_BASE_URL`/`LLM_MODEL`, код трогать не нужно.

Это ДРУГИЕ переменные, чем `VISION_LLM_URL`/`VISION_LLM_KEY` из раздела «Секреты GitHub» выше
(те — чтение этикетки на скане, `packages/cv`, секреты GitHub, приходят через `push-release.sh`;
эти — текстовый чат, `packages/llm`, секретов GitHub под них пока нет), даже если физически
указывают на один и тот же шлюз. Адрес шлюза — тоже секрет (не только ключ): в репозиторий,
GitHub Secrets проекта и чат не попадает, ставится только на сервере.

1. Получить у Вячеслава адрес шлюза (`https://<gpu-host>/v1`) и ключ.
2. Поставить вручную через SSH, оба значения — из буфера обмена, не литералом в команде (тот же
   приём, что «Запасной путь без CI» для GigaChat выше, только в два захода — под каждое значение):

```bash
pbpaste | ssh -i ~/.ssh/ci_do_ams3 somelye@89.110.72.101 'read -r u; sed -i "/^LLM_PROVIDER=/d; /^LLM_BASE_URL=/d" /opt/somelye/somelye.env; printf "LLM_PROVIDER=openai\nLLM_BASE_URL=%s\n" "$u" >> /opt/somelye/somelye.env'
pbpaste | ssh -i ~/.ssh/ci_do_ams3 somelye@89.110.72.101 'read -r k; sed -i "/^LLM_API_KEY=/d" /opt/somelye/somelye.env; printf "LLM_API_KEY=%s\n" "$k" >> /opt/somelye/somelye.env; sudo /usr/bin/systemctl restart somelye-api'
```

   `LLM_MODEL` можно не задавать — дефолт драйвера уже `qwen3.8-27b` (`somelye.env.example`,
   закомментированный блок).
3. Проверить: `sudo systemctl status somelye-api` без ошибок и живой запрос в чат через интерфейс
   стенда — ответ перестаёт быть детерминированной заглушкой мока.

Вернуть заглушку — `LLM_PROVIDER=mock` в `/opt/somelye/somelye.env` (строки `LLM_BASE_URL`/
`LLM_API_KEY` можно оставить или удалить тем же `sed -i`) и рестарт.

## Диагностика

```bash
ssh somelye@<ams3> 'sudo systemctl status somelye-api --no-pager | tail -20'
curl -s http://<ams3>/v1/healthz        # cv_index_version + warm
curl -s http://<ams3>/v1/metrics/scan   # F1 top-1/top-5 последнего прогона
```

При `OOM` в `journalctl` — убавить `MemoryMax` в юните (VPN всегда в приоритете), не наоборот.


## Индекс base-384 и слияние с текстом этикетки (с 21.09)

Индекс собран на Mac (`packages/cv/data-d1`, энкодер `google/siglip2-base-patch16-384`, эталоны после
чистки D1). Перенос на сервер — тем же `sync-data.sh`, указав индекс:

```bash
CV_INDEX_DIR=packages/cv/data-d1 infra/ams3/sync-data.sh 89.110.72.101
```

Скрипт везёт и модель base-384 (1.4 ГБ), и сырой CSV каталога для текстового индекса. В
`somelye.env` стенда — строки `CV_MODEL`, `CV_FUSION*` из `somelye.env.example`. Чтение этикетки
моделью на GPU-сервере включается секретами `VISION_LLM_URL` + `VISION_LLM_KEY`; без них слияние
работает на тексте RapidOCR (~82-90% top-1 на живых фото — см. ниже; было ~76% с PaddleOCR).

## RapidOCR вместо PaddleOCR для текста слияния (agents/H2-rapidocr-multiscale.md, с 21.09)

`CV_OCR_ENGINE=rapid` (`somelye.env.example`) переключает `read_query_text()` (текст
слияния/OCR-фолбэк, когда VLM не ответила) на RapidOCR (ONNX Runtime, детектор+распознаватель
PP-OCRv5 mobile, `CV_OCR_RAPID_SIZES=640,960` — объединение двух масштабов текстом) — на этом
4-vCPU боксе ~0.34 с на кроп 640px против 4.0 с у PaddleOCR (не зависит от бага oneDNN выше).
PaddleOCR при этом НЕ грузится в процесс вовсе, пока `CV_FUSION_VERIFY` не включён (=0 дефолт на
стенде) — near-dup verify() всё ещё PaddleOCR, но он на стенде не вызывается. `deploy.sh`
предзагружает модели RapidOCR (сеть на ams3 есть) идемпотентно ДО рестарта сервиса — первый
боевой запрос не должен ловить холодное скачивание. `CV_FUSION_CROPS=2` (не 8 — бюджет 4 vCPU,
см. `somelye.env.example`).

### Смена CV-индекса без простоя

Встроенный Qdrant читает файлы индекса напрямую, поэтому новый индекс нельзя заливать поверх
работающего. Порядок: (1) `CV_INDEX_DIR=packages/cv/data-d1 CV_INDEX_DST=cv-d1 infra/ams3/sync-data.sh <host>`
— индекс едет в `/opt/somelye/data/cv-d1`, стенд продолжает читать старый; (2) в `/opt/somelye/somelye.env`
переключить `CV_DATA_DIR=/opt/somelye/data/cv-d1` и `CV_MODEL` под энкодер индекса; (3) выкат тегом
`hack-vN` (или рестарт сервиса) — новый процесс стартует уже на новом индексе. Старый каталог
удалить после проверки `healthz` (`cv_index_version`). Шаги (2)-(3) здесь ручные — для RAG-индекса
то же самое теперь делает скрипт, см. ниже.

### Смена RAG-индекса без простоя (`switch-rag`/`rollback-rag`, с 22.09)

Тот же embedded-Qdrant, то же правило «не поверх работающего», но полностью скриптом —
`infra/ams3/sync-data.sh` умеет заливку в отдельный каталог (`RAG_INDEX_DIR`/`RAG_INDEX_DST`, по
образцу `CV_INDEX_DIR`/`CV_INDEX_DST` выше) и сам свитч:

```bash
RAG_INDEX_DIR=packages/rag/data-v2 RAG_INDEX_DST=rag-20261001 infra/ams3/sync-data.sh 89.110.72.101
infra/ams3/sync-data.sh switch-rag 89.110.72.101 rag-20261001
```

Заливка проверяет целостность (число строк `labels.jsonl` и `version` из `manifest.json` — лок./
удал. должны совпасть, иначе `exit 1` и `switch-rag` запускать рано). `switch-rag` сам бэкапит
`somelye.env` (`.bak-before-rag-<таймстамп>`), запоминает прежнее значение `RAG_DATA_DIR` на сервере
(`/opt/somelye/data/.rag_data_dir.prev`), правит `somelye.env`, перезапускает `somelye-api`, ждёт
`healthz warm:true` (тот же цикл, что `deploy.sh`) и сверяет `rag_index_version` с `manifest.json`
целевого каталога. Откат одной командой: `infra/ams3/sync-data.sh rollback-rag 89.110.72.101` —
переключает на каталог из `.rag_data_dir.prev` той же процедурой; повторный вызов — тумблер туда-
обратно. `DRY_RUN=1` перед любой из трёх форм — только печать, без изменений на диске/в env/в
сервисе. Старые каталоги (`rag/`, `rag-<версия>/`) не удаляются автоматически — только показываются
через `du -sh` в выводе `switch-rag`/`rollback-rag`, чистить вручную по своему решению.
