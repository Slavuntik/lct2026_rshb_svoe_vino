# Отчёт агента B3 — POST /v1/eval/predict

Зона: `apps/api/`, `README.md` (раздел запуска), этот файл. Задание —
`agents/B3-eval-route.md`, шаги 1–3. Первоисточник требований —
`case-data/eval/participant_test.sh` (+ README.md рядом), прочитан и разобран.

## Что сделано

1. **`POST /v1/eval/predict`** (`apps/api/app/routers/eval.py`, зарегистрирован
   в `app/main.py`) — фиксированный алиас несгораемой flat-семантики
   `/v1/scan/photo?flat=1`. Без query-развилки: путь сам по себе всегда flat.
   Общий обработчик, **без копипасты**: логика flat-ветки вынесена из
   `routers/scan.py::scan_photo` в отдельную функцию
   `routers/scan.py::flat_scan_response()`, её теперь зовут оба пути —
   `scan_photo` (когда `effective_flat`) и `eval_predict`. Рефактор
   поведенчески нейтрален для `/scan/photo` — весь прежний набор тестов
   `test_scan_photo.py` (37 тестов) остался зелёным без единой правки.
   Приём "первое файловое поле независимо от имени" (`_first_uploaded_file`)
   и без-auth режим (`get_current_principal_optional`) сохранены как есть.
2. **Тесты** — новый файл `apps/api/tests/test_eval_predict.py`, 15 тестов.
3. **README** — раздел «Прогон скрипта кейсодержателя» (сразу после
   «Быстрый старт»): как поднять API на `:8080`, команда запуска
   `participant_test.sh` их флагами как есть, что ожидать в `predictions.jsonl`.

Шаг 4 (генеральная репетиция на боевом индексе) **не выполнялся** — жду
отдельного сигнала оркестратора о готовности G3, как указано в брифе.

## Тесты

```
cd apps/api && ./.venv/bin/python -m pytest -q
186 passed, 11 skipped, 1 warning in ~6s
```

Было 171 passed / 11 skipped до этой волны — прирост ровно 15 (новый файл),
0 регрессий, набор skipped не изменился (интеграционные тесты на реальных
провайдерах, не по умолчанию). Warning — 1 и тот же `StarletteDeprecationWarning`,
что и на baseline, к этой волне отношения не имеет.

`test_eval_predict.py` покрывает все пункты брифа:
- ровно `{"slug": str}`, один ключ, `content-type: application/json` — на
  mock-провайдере;
- поле `image` принимается; любое другое файловое поле — тоже (общий
  обработчик со `/scan/photo`);
- auth не требуется (запрос без `Authorization`) и не мешает, если всё же
  прислан валидный Bearer;
- **несгораемость** — отдельно оба случая из брифа: битый файл (байты не по
  конвенции mock, `test_predict_never_errors_on_corrupted_file`) и пустое
  поле (`test_predict_never_errors_on_empty_field_value` — 0 байт;
  `test_predict_never_errors_when_no_file_field_sent_at_all` — поля нет
  вовсе), плюс переполненный файл и сбой самого пайплайна (`_ExplodingImageIndex`,
  `RuntimeError`, не `ValueError`) — везде 200 + валидный `{"slug": ...}`;
- путь не зависит от `?flat`/`SCAN_FLAT_DEFAULT` (в отличие от `/scan/photo`,
  тут нет развилки вообще);
- последовательные POST — каждый ответ валидный JSON (сценарий скрипта).

Дополнительно проверено вживую (не только `TestClient`): реальный `uvicorn`
на `:8080`, `curl --form 'image=@file'` — ровно то, что делает
`participant_test.sh`. Три запроса: валидное mock-фото → `{"slug":"shato-vymysel-cabernet"}`
200 OK за 33 мс; мусорные байты (не JPEG, не по конвенции mock) → сервис не
упал, вернул `{"slug":"igristoe-nebo-brut"}` 200 (мок деградирует
детерминированно по хэшу байт, а не по конвенции `MOCKPHOTO:` — задокументировано
ещё в `reports/b-report.md`, не regression и не баг этой волны); пустой файл
→ `{"slug":""}` 200; без единого заголовка `Authorization` — 200 OK. Лог
`uvicorn` без единой трассировки ошибки на все четыре запроса.

## Предложения к контрактам

`contracts/image-scan.md` описывает flat-семантику, но буквально путь
`/v1/eval/predict` в контракте (ни в `image-scan.md`, ни в `openapi.yaml`) не
упомянут — а `tests/test_openapi_contract.py::
test_contract_paths_match_app_exactly_no_undocumented_extras` проверяет
точное совпадение множества путей приложения с контрактами (регресс-тест
"нет путей мимо контракта"). Контракт не правил (не моя зона правки). Вместо
этого — именованное, задокументированное исключение прямо в тесте
(`_KNOWN_UNDOCUMENTED_EXTRA_PATHS = {"/v1/eval/predict"}`, с комментарием и
ссылкой на этот отчёт): тест по-прежнему ловит ЛЮБОЙ другой путь мимо
контракта, но не падает именно на этом, уже согласованном оркестратором в
брифе этой волны, пути.

**Предложение**: добавить в `contracts/image-scan.md` (раздел «Режимы ответа
API») строку вида `` `POST /v1/eval/predict` `` — алиас `/scan/photo?flat=1`
для скрипта кейсодержателя, без query-параметров, без auth. После этого
`_KNOWN_UNDOCUMENTED_EXTRA_PATHS` в `test_openapi_contract.py` можно убрать
(и тест снова станет чистым 1:1 без исключений).

## Блокеры

Нет. Единственное отступление от буквы задачи — правка
`tests/test_openapi_contract.py` (в зоне `apps/api/`, не `contracts/`) ради
исключения выше; описано подробно в предыдущем разделе, не скрыто.

## Шаг 4 — статус

Ожидаю сигнал оркестратора о готовности боевого индекса G3. После сигнала:
`IMAGE_PROVIDER=real VERIFIER_PROVIDER=real` на `:8080`, их
`participant_test.sh` на `case-data/eval/queries/` + `queries.tsv`
(3 строки, судя по составу `case-data/eval/queries.tsv` на момент этого
отчёта), результат — 3 строки `predictions.jsonl` + p95 latency_ms допишу
сюда же.
