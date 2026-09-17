# Как тестировать сканер (для Вячеслава)

Фотографируй ОДНУ бутылку крупно (этикетка занимает бо́льшую часть кадра) — это режим
кейса. Полки тоже можно, но это стресс-режим: детектор пока берёт центр кадра.

## Вариант А — самый простой (через меня)

Кидай фото в папку:

```
~/ClaudeWorkspace/vines/case-data/inbox/
```

и напиши в чат «проверь» (или «проверь inbox»). Я прогоню каждое фото через живой
пайплайн и отвечу по каждому: слаг + название + уверенность + топ-5 + вердикт
(нашли / нет в каталоге / семья near-dup). Обработанные переношу в `inbox/done/`.

## Вариант Б — сам, в браузере (наш UI)

Терминал 1 — API (реальный индекс case-20260917 + OCR-верификатор):

```bash
cd ~/ClaudeWorkspace/vines/svoy-somelye/apps/api && DATABASE_URL=sqlite:///$TMPDIR/test-drive.db JWT_SECRET=local-test-drive-secret-32-bytes-min IMAGE_PROVIDER=real VERIFIER_PROVIDER=real RAG_PROVIDER=mock CV_DATA_DIR=$HOME/ClaudeWorkspace/vines/svoy-somelye/packages/cv/data HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/uvicorn app.main:app --port 8000
```

Терминал 2 — веб-клиент:

```bash
cd ~/ClaudeWorkspace/vines/svoy-somelye/apps/web && VITE_API_MODE=real npm run dev -- --port 5173
```

Открой http://localhost:5173 → экран «Скан» → загрузи фото (галерея/камера).
Готовность API: http://localhost:8000/v1/healthz должен отдать `"warm": true`.

## Вариант В — терминал (как скрипт кейсодержателя)

При поднятом API из варианта Б:

```bash
curl -s -X POST "http://localhost:8000/v1/scan/photo?flat=1" -F "image=@/путь/к/фото.jpg"
```

Ответ — ровно `{"slug":"..."}`. Полная карточка с уверенностью и топ-5 — без `?flat=1`.

## Что считать хорошим результатом

- Вино каталога «Своё Вино» (российское из отбора Роскачества, 2103 позиции) →
  правильный слаг в топ-1, или хотя бы в топ-5 (видно в rich-ответе).
- Импорт/крепкое/не вино → «нет в каталоге» + разумные похожие.
- Одна серия, разные годы (Массандра, Аристов…) → сейчас честная зона роста:
  различает OCR-верификатор, его точность калибруем на полевых данных.
