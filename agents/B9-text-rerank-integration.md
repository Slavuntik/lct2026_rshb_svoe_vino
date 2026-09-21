# Агент B9 · Встраивание текстового переранжирования в сервис (контракт v0.4.12)

**Зона:** `apps/api/`, в `packages/cv/` — ТОЛЬКО `cv/verify.py` (обратно совместимое
расширение сигнатуры), `reports/b9-text-rerank.md`. Коммиты только с pathspec.
Параллельно агент G6 работает в packages/cv над энкодером (encoder.py/index.py/новые файлы) —
эти файлы не трогать.

## Задачи

1. Прочитай contracts/image-scan.md §v0.4.12, `packages/cv/cv/text_rerank.py` (G5) и
   `reports/g5-accuracy.md` (рекомендованные K=5, w=0.01, ocr_crop, порог min_token_idf).
2. `LabelVerifier` (packages/cv/cv/verify.py): публичный метод чтения текста запроса по кропу
   normalize_query (переиспользуй read_text) и необязательный параметр `ocr_text` у `verify()`
   — если передан, OCR не повторяется. Старое поведение без параметра — бит в бит.
3. apps/api: интерфейс верификатора (app/cv/interface.py) получает метод чтения текста; mock —
   сценарный (например, `MOCKPHOTO:ocr:<текст>:<slug>` или отдельный хук) для тестов.
4. `run_photo_scan` (app/cv/service.py): при `settings.cv_text_rerank` — OCR один раз, затем
   `text_rerank` по top-K схлопнутых кандидатов; тексты кандидатов из каталога кейса
   (`app/rag/case_catalog.py` от B8: name, winery_name, grapes, region_name), фолбэк — наш
   каталог. Тот же OCR-текст — в verify(). flat и rich — один порядок.
5. Settings: `CV_TEXT_RERANK` (дефолт false), `CV_TEXT_RERANK_K` (5), `CV_TEXT_RERANK_W` (0.01).
6. Тесты: выключено — поведение прежнее бит в бит; включено + информативный OCR — кандидат
   поднимается; неинформативный OCR — порядок CV; OCR выполняется ровно один раз на запрос;
   flat и rich согласованы. Полный свод api (269/11) и cv (158) зелёные.
7. Замер задержки на Mac с реальным провайдером: живой API НЕ поднимай, если лок индекса
   занят G6 — тогда замерь только стадию OCR+rerank напрямую и честно отметь в отчёте.

## Не делать

Пороги гейта, flat/eval-семантику, индекс, стенд — не трогать. ORCHESTRATION.md — фон.
