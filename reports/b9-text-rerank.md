# B9 · Встраивание текстового переранжирования в сервис (контракт v0.4.12)

## Пайплайн (встроено)

```
ANN search (image_index.search)
  → [CV_TEXT_RERANK=1] verifier.read_query_text(image)  — OCR, РОВНО ОДИН РАЗ
  → [CV_TEXT_RERANK=1] cv.text_rerank.rerank_top_k(matches[:K], ocr_text, catalog, idf, w)  — только порядок matches
  → near-dup candidate_slugs (CV_VERIFY_PROXIMITY, без изменений)
  → verifier.verify(image, candidates, ocr_text=ocr_text)  — тот же текст, второго OCR нет
  → confident/not_in_catalog (CV_ABS_FLOOR/CV_MARGIN_FLOOR, без изменений)
```

`ocr_text` — `None`, если `CV_TEXT_RERANK=0` (дефолт): `verify()` тогда сам читает OCR,
код идёт ровно по старой ветке — бит в бит.

## Сделано

1. **`packages/cv/cv/verify.py`** (единственный файл вне apps/api, разрешённый брифом):
   - `LabelVerifier.read_query_text(image: bytes) -> str` — публичный метод: decode →
     `normalize_query()` (тот же кроп, что видит `ImageIndex.search()`) → `read_text()`.
     Ровно та цепочка, что раньше была инлайн внутри `verify()`.
   - `LabelVerifier.verify(image, candidates, ocr_text: str | None = None)` —
     необязательный именованный параметр в конце сигнатуры (позиционные вызовы `verify(image,
     candidates)` не меняются вовсе). `ocr_text is not None` → используется как есть,
     `read_text()` не вызывается (OCR не повторяется); пустая строка `""` — ВАЛИДНОЕ
     "передано, но нечего сопоставлять" и тоже не трогает OCR (отличается от `None` =
     "не передано, прочитай сам"). `arr = imageio.decode_image(image)` остался
     БЕЗУСЛОВНЫМ первым шагом (как и раньше) — `ValueError` на битых байтах не зависит
     от того, передан ли `ocr_text`. Ветка `ocr_text is None` — код, БУКВАЛЬНО не
     изменившийся посимвольно относительно версии до правки (не переписан заново, а
     оставлен как есть внутри `if/else`) — гарантия "бит в бит" по построению, не по
     совпадению поведения.
2. **`apps/api/app/cv/interface.py`** — `LabelVerifier` Protocol зеркалит обе правки
   (`read_query_text`, `verify(..., ocr_text=None)`) — сверено посимвольно с packages/cv.
3. **`apps/api/app/cv/mock.py`** — `MockLabelVerifier`:
   - `read_query_text()` — сценарный хук `MOCKPHOTO:ocr:<текст>` → `<текст>` дословно;
     любой другой вход (остальные MOCKPHOTO-формы, настоящее фото) → `""` (честно "OCR
     ничего не читал", text_rerank тогда no-op).
   - `verify(..., ocr_text=None)` — параметр принят и осознанно проигнорирован (решение
     мока по-прежнему по байтам `image`, не по тексту) — существующие near-dup сценарии
     не меняются НИ НА БИТ.
4. **`apps/api/app/cv/service.py`** — `run_photo_scan()`:
   - Один проход OCR (`verifier.read_query_text(image_bytes)`) СРАЗУ после ANN, ДО
     near-dup routing, только если `settings.cv_text_rerank`.
   - `_apply_text_rerank()` зовёт `cv.text_rerank.rerank_top_k()` по top-`CV_TEXT_RERANK_K`
     схлопнутых `matches`; переставляет ТОЛЬКО порядок списка — каждый `Match` несёт
     свои ИСХОДНЫЕ `score/gap/view` (не blended cv+w·text) — пороги гейта
     (`CV_ABS_FLOOR`/`CV_MARGIN_FLOOR`) читают `top.score`/`top.gap` того матча, что
     оказался на позиции 0 после переранжирования; сама калибровка порогов не тронута
     (бриф: "пороги гейта... не трогать").
   - Текст кандидата для rerank — `_text_rerank_catalog_entry()`: каталог кейса
     (`app/rag/case_catalog.py::lookup()`, name/winery_name/grapes/region_name) первым
     делом, фолбэк — наш RAG-каталог (`retriever.get_by_id()`), тот же порядок
     источников, что уже использует `_candidate_item()` (v0.4.11). Слаг, неизвестный
     нигде, — честная деградация (`CatalogText` с пустыми полями, `text_score()`=0,
     не роняет остальных).
   - IDF — корпус ВСЕГО каталога кейса (`app/rag/case_catalog.py::all_slugs()`, новая
     публичная функция, +9 строк, поведение `lookup()` не тронуто), не только top-K
     запроса (иначе "редкий токен" не имеет смысла на 5 документах) — кэш
     `@lru_cache`, ключ `str(case_data_dir())` (тот же паттерн, что `_load_catalog`/
     `_load_mapping` в обоих модулях `case_catalog.py`).
   - `cv.text_rerank` импортируется ЛЕНИВО (внутри функции, не на верху модуля) —
     тот же принцип, что `app/cv/factory.py` уже применяет к `cv.index`/`cv.verify`:
     пока `CV_TEXT_RERANK=0` (дефолт), apps/api НЕ требует тяжёлый packages/cv
     (torch/paddleocr транзитивно) — базовый тестовый свод (269/11 до этой волны) этот
     импорт не видит вовсе. `ImportError` → внятный `RuntimeError` с командой установки
     (симметрично `factory.py`).
   - `verifier.verify(..., ocr_text=ocr_text)` — тот же прочитанный текст уходит в
     near-dup верификатор; `ocr_text=None`, если флаг выключен → verify() читает сам.
5. **`apps/api/app/config.py`** — `CV_TEXT_RERANK` (bool, дефолт `False`),
   `CV_TEXT_RERANK_K` (int, дефолт `5`), `CV_TEXT_RERANK_W` (float, дефолт `0.01`) —
   ровно рекомендация G5 (`reports/g5-accuracy.md`, holdout n=374).

## Тесты

### packages/cv

```
cd packages/cv && HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/pytest -q
```
**166 passed** (было 158 — +8 новых, 0 регрессий, ~30с). Новые тесты —
`packages/cv/tests/test_verify.py`, секция "v0.4.12": `ocr_text` никогда не трогает
`read_text()` (движок не загружается — `v._ocr is None`); пустая строка = "передано, не
`None`"; без параметра — `read_text()` по-прежнему зовётся РОВНО один раз (регресс "бит в
бит"); `ocr_text=None` явно = то же самое, что параметр не передан; битые байты →
`ValueError` даже когда `ocr_text` дан; debug-трассировка несёт ПЕРЕДАННЫЙ текст дословно,
OCR не трогает; `read_query_text()` даёт ПОБИТОВО ту же строку, что ручной
`decode→normalize_query→read_text` (реальный PaddleOCR); сильное доказательство —
фото реально показывает год 2025, но переданный `ocr_text` со годом 2026 передвигает
решение НА 2026-кандидата, не на то, что реально на фото.

### apps/api

```
cd apps/api && .venv/bin/pytest -q
```
**276 passed, 11 skipped** (было 269/11 — +7 новых, 0 регрессий, ~7с). Побочная правка:
`_SpyLabelVerifier` (тестовый дубль в `test_scan_photo.py`) получил `ocr_text=None` в
`verify()` и новый `read_query_text()` — без неё 6 СУЩЕСТВУЮЩИХ near-dup тестов
ломались об `unexpected keyword argument 'ocr_text'` в момент, когда `run_photo_scan`
начинает передавать этот kwarg безусловно; сам факт поймал это регрессией сразу (см.
"Блокеры" ниже — не блокер, а иллюстрация, что полный прогон реально проверяет).

Новая секция `test_scan_photo.py` (v0.4.12, 8 тестов, включая пин дефолтов):
пять слагов с непересекающимися по токенам винодельнями и тесным кластером ANN-score
(шаг 0.001) — `rerank-candidate-delta` (винодельня "Погреб Дельта", 4-й по сырому score)
поднимается на top-1 при информативном OCR ("Погреб Дельта"), арифметика доказана в
докстринге теста (idf_overlap=1.0+rapidfuzz=100% для delta против idf_overlap=0 у
остальных четырёх — гарантия победы не зависит от точного числа rapidfuzz на
несвязанных строках); пустой OCR → порядок ANN не меняется; выключенный флаг → тот же
сценарий НЕ меняет порядок И `read_query_text()` не вызывается вовсе (не только
результат не меняется — сам механизм не трогается); OCR ровно один раз на запрос
(`spy.read_query_text_calls == 1`), и `verify()` получает РОВНО тот же текст
(`spy.ocr_texts == [<текст>]`); flat и rich — независимые запросы, один и тот же
промотированный слаг; слаг вне обоих каталогов (кейс+RAG) внутри reranked top-K не
роняет пайплайн (честная деградация).

```
cd apps/api && uv lock --check   # "Resolved 134 packages" — без изменений (pyproject.toml/uv.lock не трогал)
```

## Подтверждение «OCR один раз на запрос»

`test_text_rerank_ocr_happens_exactly_once_per_request_and_is_reused_by_verifier`
(`apps/api/tests/test_scan_photo.py`) — сценарий, где ВСЕ 5 top-K кандидатов попадают в
`CV_VERIFY_PROXIMITY` (гарантированно зовёт `verifier.verify()` вторым потребителем
текста): `spy.read_query_text_calls == 1` и `spy.ocr_texts == [<тот же текст>]` —
`verify()` не читает OCR заново, использует переданный. На стороне `packages/cv`:
`test_verify_without_ocr_text_argument_still_calls_read_text` (счётчик вызовов
`read_text` == 1 без параметра) + вся секция "никогда не трогает read_text()", когда
параметр дан, — то же утверждение с обеих сторон шва.

## Замер задержки стадии OCR+rerank

Бриф: "живой API НЕ поднимай, если лок индекса занят G6 — тогда замерь только стадию
OCR+rerank напрямую". За время сессии G6 реально работал параллельно в `packages/cv`
(видел его правки `.gitignore` и новые скрипты появляться в `git status` по ходу дела) —
поднимать живой API с `IMAGE_PROVIDER=real` поверх `packages/cv/data/qdrant` не стал
осознанно, не только по формальному условию "лок занят": embedded-Qdrant держит
эксклюзивную блокировку каталога на всё время жизни процесса — даже кратковременный
живой прогон рискует столкнуться с G6 ровно на границе, а сама задача просит замер
ИМЕННО стадии OCR+rerank, не полного пайплайна (тот уже есть в `reports/g5-accuracy.md`).
Замерил напрямую: `cv.verify.LabelVerifier.read_query_text()` (реальный PaddleOCR) +
`cv.text_rerank.rerank_top_k()` (реальный каталог `strapi_output0709.csv`, 2103 слага,
K=5, w=0.01) — БЕЗ ImageIndex/qdrant вообще, скрипт и полный вывод — в отчёте ниже.

Фото — те же 6, что использовал G5 (`reports/g5-accuracy.md`): 3 `case-data/eval/queries/*`
+ 3 реальных скана стенда (`case-data/stand-scans/{confident,failed}/20260921/*.jpg`),
только чтение.

| файл | OCR мс | rerank мс | информативно |
|---|---:|---:|:---:|
| 019c68d0.jpg | 796.6 | 0.113 | нет |
| 02eef911.webp | 987.4 | 0.178 | да |
| 096ca74e.jpg | 600.4 | 0.028 | нет |
| stand-scan confident/…65779c.jpg | 931.6 | 0.124 | да |
| stand-scan failed/…35baff.jpg | 682.5 | 0.037 | нет |
| stand-scan failed/…87a211.jpg | 567.6 | 0.026 | нет |

Прогрев (первый `read_text()`, ленивая загрузка PaddleOCR, ОДНОРАЗОВО на процесс, гасится
штатным `warm_up_label_verifier()` на старте — contracts v0.4.7 §5): **3082 мс**, отдельно
от стадии, не входит в числа ниже.

**OCR (тёплый движок):** p50=739.6мс, max=987.4мс, mean=761.0мс (n=6).
**rerank:** p50=0.075мс, max=0.178мс, mean=0.085мс — пренебрежимо мал относительно OCR,
согласуется с G5 ("rerank ~0/0.2 мс").
**OCR+rerank суммарно:** p50=739.6мс, max=987.6мс.

Честная поправка к G5 (`reports/g5-accuracy.md` мерил 294/554 мс p50/p95 на своих
синтетических near-dup картинках 300×630): РЕАЛЬНЫЕ телефонные фото (1.2–3.5 МБ,
полное разрешение, сложный фон до кропа) дают заметно бОльшую стадию OCR — p50 740мс,
не 294мс. Это тот же эффект, что G5 уже задокументировала для ИНФОРМАТИВНОСТИ OCR
("синтетика занижает пользу") — этот замер показывает, что синтетика занижает ещё и
ЗАДЕРЖКУ, не только пользу. И то, и другое — в пользу решения "включать только после
проверки на реальных фото", уже зафиксированного в контракте v0.4.12 п.5. Полный
пайплайн ≤3с (контракт DoD) на Mac по-прежнему держится с запасом даже на этих числах:
даже худший случай (987мс OCR) плюс decode/normalize/embed/ANN G5 (≈56мс) — около 1с,
не считая маржи near-dup verify() (переиспользует тот же текст, доп. стоимости почти
нет — сопоставление токенов на CPU, не OCR).
Скрипт замера — вне git (разовый, `/private/tmp/.../scratchpad/b9_ocr_rerank_bench.py`),
текст приведён по запросу оркестратора при необходимости.

## Не делал (по брифу)

Пороги гейта (`CV_ABS_FLOOR`/`CV_MARGIN_FLOOR`/`CV_VERIFY_PROXIMITY`), flat/eval-семантику,
`packages/cv/cv/index.py`/`encoder.py`/индекс, стенд — не трогал. Файлы G6
(`encoder.py`, `index.py`, новые модули, `data-exp/`) — не трогал (подтверждено diff:
единственный тронутый файл в `packages/cv/cv/` — `verify.py`).

## Блокеры

Нет.

## Предложения к контрактам

Нет — v0.4.12 оказалась однозначной для встраивания, дыр не нашёл.

## DoD (agents/B9-text-rerank-integration.md)

- [x] `LabelVerifier.read_query_text()` (публичный, переиспользует `read_text`) +
      `verify(..., ocr_text=None)` — старое поведение бит в бит без параметра.
- [x] apps/api: `LabelVerifier` Protocol получил оба изменения; mock — сценарный
      (`MOCKPHOTO:ocr:<текст>`) + осознанно проигнорированный `ocr_text` в `verify()`.
- [x] `run_photo_scan`: OCR один раз при `CV_TEXT_RERANK`, `text_rerank` по top-K,
      тексты кандидатов — каталог кейса с фолбэком на наш каталог, тот же `ocr_text` — в
      `verify()`; flat и rich — один порядок (общий список `matches` реордерится один раз
      до обеих веток ответа).
- [x] Settings: `CV_TEXT_RERANK`(false)/`CV_TEXT_RERANK_K`(5)/`CV_TEXT_RERANK_W`(0.01).
- [x] Тесты по всем 5 пунктам брифа (выключено бит-в-бит / промоушен при информативном
      OCR / порядок CV при неинформативном / OCR ровно один раз / flat=rich) — и в
      packages/cv, и в apps/api. Полные своды api (276/11) и cv (166) зелёные.
- [x] Замер задержки — напрямую (стадия OCR+rerank, без ImageIndex/qdrant), с честной
      причиной почему не живой API (лок embedded-Qdrant, G6 активен параллельно).
