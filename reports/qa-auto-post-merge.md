# qa-auto: приёмка main после мержа 123 коммитов Михаила (26.09)

Проверено на `6538c26` (HEAD прогона; после — 2 коммита architect, `git diff 6538c26..0a9e55e
--stat` = только .gitignore/ARCHITECTURE.md/README.md/contracts/image-scan.md/PDF, боевой код
не тронут). **Вердикт: approve на выкат hack-v18**, один пункт требует перепрогона (см. «Тимлиду»).

## 1. Тесты — все зелёные, регрессий от мержа нет
`apps/api` 553 passed/12 skipped (было 539/12, +14 — новые `test_scan_user_box.py`/
`test_winescan_evidence.py`) · `packages/cv` 422 passed (было 422, код не менялся) ·
`packages/rag` 115 passed (было 115, код не менялся) · `packages/llm` 32 passed (без изменений) ·
`apps/web` 124 passed/21 файл, `npm run build` OK (было 112/17). Числа сверены дважды (свой
прогретый venv + фоновый агент в чистом worktree) — сошлись побитово. Два флака вне мержа, не
блокер: `cv::test_verify_p95_latency_budget` красный только в холодном процессе (PaddleOCR
грузится внутри окна замера), в тёплом venv — чисто; `rag` даёт exit 134 (нативный краш
`libc++abi` при выгрузке onnxruntime) только в свежем venv на macOS, у меня чисто, код не менялся.
`tools/sync_winescan.py --check` зелёный; `packages/winescan` — не в CI, не блокер.
**Расхождение**: `reports/architect-post-merge-review.md` даёт «факт 524/115/104» api/rag/web —
мои 553/115/124 воспроизведены дважды независимо; для README (его сейчас переписывает architect)
предлагаю мои числа.

## 2. Живой API (127.0.0.1:8772, копии индексов `qa-auto-hack-v16-{cv-data-d1,rag-data}` без
`.lock`, env = `infra/ams3/somelye.env.example` дословно, не Mac-профиль run-check-server.sh) —
**top-1 59/62 (95.2%)**, 100/100 без HTTP-ошибок. Единственное расхождение внутри 62 —
`94.55_02-09-2026_16-53-08.webp` (истина `denisov_pazori_risling`) — **не регрессия кода**:
`_run_photo_scan_fusion()` (реальная ветка при `IMAGE_PROVIDER=real`+`CV_FUSION=1`) побитово не
менялась с hack-v17. Причина — **действующий сбой GPU-шлюза**: 7 таймаутов VLM + 4 срабатывания
предохранителя за прогон, 75/100 фото ушли fallback'ом (<2с); повтор фото и прямой запрос
`/chat/completions` виснут >10с, а `/models` отвечает за 1с (шлюз жив, инференс — нет). Совпадает
с гипотезой architect (п.4 его отчёта: «0 расхождений от кода, но это гипотеза для qa-auto»).

## 3. Задержки flat — p50 1521/p95 4348/max 6431 мс (hack-v16: 4348/5203/6568), 0 фото дороже
10с. Число НЕ сравнимо впрямую — 75/100 запросов ушли быстрым fallback'ом (п.2), а не полным
VLM-путём. Кода, способного замедлить путь `real`+`CV_FUSION`, не нашёл (новые проверки — атрибуты,
без I/O). Нужен чистый перезамер после восстановления шлюза.

## 4. Контракт — `/v1/eval/predict` отдаёт ровно `{"slug":...}` (жюри-путь не тронут). Rich-ответ
и `card` совпадают с v0.4.18 один в один; `box` работает по контракту (нет поля → без изменений,
валидный → крой до движка, невалидный → 400 rich/молча игнорируется flat). **Предложение к
контрактам**: `contracts/openapi.yaml` (0.3.6) у `/scan/photo` в multipart-схеме всё ещё только
`image` — `box` (v0.4.18) не добавлен; `test_openapi_contract.py` этого не ловит (сверяет
пути/методы и поля ответа `/scan/resolve`, не request body `/scan/photo`) — предлагаю architect
дописать. Дубль версии v0.4.10 architect уже разобрал (v0.4.18).

## 5. `apps/shelf-finder` — самоизолирован: свой Vite (5180/4180) и API (8086), наши порты
8080/8091/8093/8772 не задеты (проверено живьём). Не использует наши индексы. Frontend стартует
чисто. Полный серверный движок требует **NVIDIA GPU 24 ГБ VRAM + CUDA** (`server/README.md`) —
на Mac структурно невозможен. Продакшн-роутинг `/app/shelf` отдельно разобран architect
(`reports/architect-post-merge-review.md` п.3) — вне зоны этой проверки.

## Как воспроизвести
Лаунчер по образцу `infra/local-check/run-check-server.sh`, env из `infra/ams3/somelye.env.example`
(порт 8772, копии индексов). `qa/real_photos_serve.py --flat-only --api http://127.0.0.1:8772
--out case-data/real-photos-labels/served/qa-post-merge-main.jsonl`, затем `cd packages/cv &&
.venv/bin/python ../../qa/real_photos_eval.py --only zzz --served ../../../case-data/
real-photos-labels/served/{qa-post-merge-main,stand-hack-v16}.jsonl`.

## Тимлиду
Код безопасен для hack-v18 — просадка 59/62 объясняется живым сбоем GPU-шлюза, не мержем (диф
+ логи). Прошу: (1) перед тегом — контрольный `--flat-only` прогон 100 фото на стенде, когда шлюз
подтверждённо ответит на `/chat/completions` (сейчас виснет >10с); (2) architect дописать `box`
в `openapi.yaml`; (3) сверить числа тестов (553/115/124) с переписываемым README.
