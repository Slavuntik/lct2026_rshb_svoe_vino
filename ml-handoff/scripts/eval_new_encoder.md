# eval_new_encoder.md — runbook: заменить `CV_MODEL`/чекпойнт и измерить

Пошаговая инструкция для ML-команды: как честно сравнить новый энкодер (другой
готовый чекпойнт ИЛИ свой fine-tuned/adapter) с зафиксированным baseline
(`../BASELINES.md`), не трогая ничего чужого. Общие принципы — `../EVAL.md`
("eval-gate"); подводные камни — там же §4.

**Перед началом**: если на машине есть живой стенд на известном порту (на момент
сборки этого пакета — `:8000`), НЕ гасить и НЕ перезапускать его — все шаги ниже
поднимают СВОЙ процесс на свободном порту и гасят именно его.

## 0. Подготовка

```bash
cd svoy-somelye
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
```

Новый чекпойнт/адаптер должен быть уже локально доступен (HF-кэш, локальный путь —
`CV_MODEL` принимает и то, и другое, `packages/cv/cv/encoder.py::AutoModel.
from_pretrained`) — офлайн-флаги выше делают сетевые обращения ошибкой, а не
скрытой задержкой, если что-то не докачано.

## 1. Юнит-тесты packages/cv — прежде чем считать что-то на реальных данных

```bash
cd packages/cv && ./.venv/bin/python -m pytest -q
```

Новый чекпойнт с другой размерностью эмбеддинга/другим препроцессингом иногда ломает
неявные допущения (`cv/encoder.py::dim`, кэш-путь по имени модели) — тесты должны
остаться зелёными ДО того, как тратить 10+ минут на сборку индекса.

## 2. Собрать НОВЫЙ индекс — НЕ перезаписывать боевую версию

```bash
export CV_MODEL=<новый чекпойнт, HF id или локальный путь>
REFS=<CASE_DATA_DIR>/slug_refs.json
UPLOADS=<CASE_DATA_DIR>/prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads
VERSION="case-$(date +%Y%m%d)-<короткое-имя-модели>"   # НЕ "case-20260917" — та уже занята

python3 -m cv.cli build-index --refs-json "$REFS" --uploads-dir "$UPLOADS" \
  --version "$VERSION" --views 24 --out "data/build_summary_${VERSION}.json"
```

Ожидаемое время — см. `../BASELINES.md` §2 (≈46 мин на 1676 позиций/25 ракурсов на
референсной машине; для 1982 usable — дольше, эмбеддинг-кэш общий, если модель та
же, что раньше, но для НОВОЙ модели кэш холодный, дороже не будет). Синхронно ждать
завершения в ТОМ ЖЕ вызове (ORCHESTRATION.md — не отвязанный `nohup`).

## 3. Self-match на новом индексе — apples-to-apples с `case-20260917`

```bash
python3 -m cv.cli selfcheck --refs-json "$REFS" --uploads-dir "$UPLOADS" \
  --views 2 --sample-n 300 --sample-seed 0 --out "data/selfcheck_${VERSION}.json"
```

**Тот же `--sample-seed 0`**, что и baseline `case-20260917` (75,2%/88,2%,
`../BASELINES.md` §2) — иначе сравнение сравнивает разные выборки, а не разные модели.

## 4. Поднять API на новом индексе — СВОЙ порт, синхронный health-poll

```bash
cd ../../apps/api
CV_DATA_DIR=<абсолютный путь к packages/cv/data новой версии, если хранится отдельно> \
IMAGE_PROVIDER=real VERIFIER_PROVIDER=real RAG_PROVIDER=mock \
  ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port <свободный_порт> --log-level warning &
UVICORN_PID=$!

for i in $(seq 1 30); do
  curl -sf "http://127.0.0.1:<свободный_порт>/v1/healthz" | grep -q '"warm":true' && break
  sleep 1
done
curl -s "http://127.0.0.1:<свободный_порт>/v1/metrics/scan"  # проверить cv_index_version == $VERSION
```

Синхронный poll в том же вызове — ORCHESTRATION.md ("длинный шаг либо ждать
СИНХРОННО циклом в том же bash-вызове"), не отвязанный фон.

## 5. Прогнать eval

```bash
cd ../../
python qa/scan_eval.py --photos-dir <публичный набор>/dev --mode rich \
  --api-url http://localhost:<свободный_порт> --split dev \
  --out-dir ml-handoff-eval-runs/<VERSION>   # НЕ qa/scan-eval-runs (чужая зона)
```

Если полевой датасет ещё не приехал (DATA.md §1/§7) — прогон на синтетике
(seed **НЕ** из списка "сожжённых", DATA.md §6) с явной пометкой в отчёте
"synthetic, not field" — тем же принципом, что `../reports/f3-synthetic-baseline.md`.

## 6. Сравнить с BASELINES.md — решение о публикации

- raw top-1/top-5, match-rate top-1/top-5, F1 — рядом со строкой `../BASELINES.md`
  §3/§4 (тот же индекс/выборка/seed, если синтетика) или новой строкой в holdout-разделе
  (если реальный полевой прогон).
- p50/p95 — не хуже SLA ≤3с (`case.md`); сравнить и с `../BASELINES.md` §6.
- **Публиковать/переключать `CV_MODEL` только при неухудшении** (или явно
  обоснованном компромиссе) — см. `../EVAL.md` §3.

## 7. Погасить временный процесс, освободить ресурсы

```bash
kill "$UVICORN_PID"
wait "$UVICORN_PID" 2>/dev/null
lsof -nP -iTCP:<свободный_порт> -sTCP:LISTEN   # пусто — порт свободен
lsof +D packages/cv/data/qdrant                # пусто — лок свободен (если писали в общий путь)
```

Подтвердить ОБА пустых вывода перед тем, как считать шаг завершённым — та же
дисциплина, что `../reports/f3-synthetic-baseline.md` §1.5/`../reports/
b4-gate-v047.md` "Завершение".

## 8. Записать результат

Новая строка в `../BASELINES.md` (метрики + коммит/дата + `$VERSION`), ссылка на
использованный чекпойнт/адаптер и на использованный eval-сет (синтетика с каким seed,
или полевой dev/holdout). Старую боевую версию индекса (`case-20260917`) не удалять —
откат должен оставаться одной командой `cv build-index ... --version case-20260917`
(она уже существует, если не была перезаписана) или, если удалена — пересборкой по
тем же `slug_refs.json`/`families.json` с `AUGMENT_SEED_DEFAULT=0` (DATA.md §6).
