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
- **Откат:** `DEPLOY_REF=<коммит> bash infra/ams3/push-release.sh <ams3>`.

## Секреты GitHub

Ровно три, тип **Secrets** (не Variables — workflow читает `secrets.*`, переменная из Variables
придёт пустой), уровень **Repository** (не Environment — деплой-джоба не объявляет
`environment:`, секреты окружения ей не видны). Settings → Secrets and variables → Actions →
Secrets → New repository secret:

| Имя | Значение |
|---|---|
| `AMS3_HOST` | `89.110.72.101` |
| `AMS3_SSH_KEY` | приватная половина ключа CI целиком, со строками BEGIN/END: `pbcopy < ~/.ssh/ci_do_ams3` |
| `AMS3_KNOWN_HOSTS` | вывод `ssh-keyscan 89.110.72.101` целиком |

Пользователь `somelye` зашит в `push-release.sh` — отдельный секрет не нужен. Без секретов
workflow не падает, а вежливо пропускает выкат (джоба `guard`).

## Диагностика

```bash
ssh somelye@<ams3> 'sudo systemctl status somelye-api --no-pager | tail -20'
curl -s http://<ams3>/v1/healthz        # cv_index_version + warm
curl -s http://<ams3>/v1/metrics/scan   # F1 top-1/top-5 последнего прогона
```

При `OOM` в `journalctl` — убавить `MemoryMax` в юните (VPN всегда в приоритете), не наоборот.
