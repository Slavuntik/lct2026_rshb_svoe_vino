# Architect · контракт «после скана» — гастропары + «похоже по вкусу» (22.09)

Задача тимлида 22.09: экран после скана — «плюшки» из `docs/product/post-scan.md`
(«Функция после поиска» 20/100 в кейсе, `case.md` строки 13/84). Контракт спроектирован
и ратифицирован: `contracts/post-scan.md` v1.0, `contracts/openapi.yaml` → 0.3.3
(+`GET /v1/wines/{wine_id}/pairings`). Ничего не реализовано — ниже брифы для backend и
frontend, готовые к выдаче как есть.

## Находка, меняющая буквальную постановку

Бриф предполагал ключи «сорт/цвет/**сахар**/категория карточки». Уровень сахара
СТРУКТУРНО отсутствует у карточки кейса — в `strapi_output0709.csv` нет такой колонки
(`apps/api/scripts/build_case_catalog.py:17-36`); поле `category` там — оттенок в бокале,
не сахар. Контракт спроектирован на том, что реально есть: `color` (4 значения),
`grapes`, свободный текст `name`/`description`. Подробности и таблица дефолтов —
`contracts/post-scan.md` §1.

## Решение по форме контракта

Новый эндпоинт (не поле в карточке) — `card` уже заморожен с v0.4.1 (`image-scan.md`:
«ровно тело ответа GET /wines/{id}»), трогать его форму ради необязательного блока —
лишний риск. Три уровня данных с явным `basis` (catalog/sensory/heuristic/unavailable) —
чтобы фронт не гадал, откуда взялись теги, и честно показывал «нет данных», когда так и
есть (инструкция тимлида, п.1а).

---

## Задача backend: `GET /v1/wines/{wine_id}/pairings`

Контракт: `contracts/post-scan.md` §1 (схема, алгоритм, таблица дефолтов, ключевые слова —
уже всё посчитано и сформулировано, включая формулу скоринга и дословный список
операторов мини-DSL `food_pairing_rules.yaml`). Схема ответа — `contracts/openapi.yaml`
`/wines/{wine_id}/pairings`.

**Сделать:**
1. Резолюция `wine_id` — переиспользовать `apps/api/app/rag/cards.py::build_wine_card`
   (не писать вторую копию резолюции наш-каталог/каталог-кейса). 404 `not_found` на тех
   же условиях, что `GET /wines/{id}`.
2. Три уровня по `contracts/post-scan.md` §1: catalog (`source.food_pairings` как есть) →
   sensory (`derived.sensory`, ось `wine.abv_percent` — отдельно из `source.abv_percent`,
   дефолт 12.5 если нет) → heuristic (таблица цвет×подрежим + ключевые слова сахара/
   игристости из `name`+`description`, см. точные списки в контракте) → unavailable
   (пустой `color`).
3. Мини-DSL правил `food_pairing_rules.yaml`: один общий `eval_condition(dict, dish, wine)`
   для `when`/`require`/`penalize`/`hard_blocks.condition` (последний смешивает `dish.*` и
   `wine.*` в одном словаре — намеренно, не два разных обработчика). Операторы: `>=X`,
   `<=X`, `~=dish.Y ± Z`, `>=dish.Y` (сверка с другим полем блюда), `matches wine.region`
   (никогда не сработает — не баг). Формула и правило top-3/tie-break — контракт §1, п.
   «Правила скоринга».
4. Место хранения таблицы дефолтов/ключевых слов уровня heuristic — на ваше усмотрение
   (новая секция `pipeline/ref/food_pairing_rules.yaml`, по прецеденту
   `reference_styles.yaml`/`grape_synonyms.yaml`, ЛИБО константа в `apps/api/app/rag/`) —
   `pipeline/ref/` не назначена ничьей зоной в `TEAM.md` явно, зафиксируйте выбор в своём
   отчёте (см. «Вопрос тимлиду» ниже).

**Тесты (`apps/api/tests/test_wine_pairings.py`, паттерн — `test_wines.py`:
`register_user`/`auth_header`/`client.get("/v1/...")`):**
- catalog: вино наших фикстур с непустым `food_pairings` → `basis="catalog"`, `pairings`
  повторяет список, `score=null`.
- sensory: вино с `derived.sensory`, без `food_pairings` → `basis="sensory"`, `len<=3`,
  `score∈[0,1]`, `triggered_rules` непусты при `score>0`.
- heuristic: фикстура каталога кейса (`CASE_DATA_DIR` monkeypatch, есть `color`, нет
  сенсорики) → `basis="heuristic"`.
- unavailable: та же фикстура, но `color=""` → `pairings=[]`, `message` непуст.
- 404: неизвестный `wine_id` ни в RAG, ни в case-каталоге.
- детерминизм: два вызова подряд — идентичный ответ (без случайности; alphabetical
  tie-break реально проверить, подобрав 2 тега с равным score).
- хотя бы 1 сценарий на `hard_blocks` (тег исключён, даже если он был бы топ по score).
- ключевые слова: `name` с «брют» → sweetness-прокси 0.05 независимо от дефолта цвета;
  `name` с «игристое» → строка bubbles игристого подрежима.
- `cd apps/api && .venv/bin/pytest -q` — зелёный, включая существующие тесты (регрессий нет).

**Приёмка:** все пункты выше проходят; ответ валиден по схеме `openapi.yaml`
(`/wines/{wine_id}/pairings`); задача НЕ считается сделанной без heuristic-ветки — это
единственный путь, реально работающий на приватной проверке (только каталог кейса).

---

## Задача frontend: рендер гастропар + «похоже по вкусу»

Контракт: `contracts/post-scan.md` §2. Backend-часть — только новый GET из задачи выше;
`/v1/analogs` и `/v1/taste/*` не меняются вообще.

**Сделать:**
1. `apiClient.getWinePairings(wineId)` в `apps/web/src/lib/apiClient.ts` (по образцу
   `getWine`) + тип `WinePairingsResponse` в `apiTypes.ts` (`wine_id/basis/pairings/
   message`, зеркалит схему `openapi.yaml`).
2. Блок гастропар — внутри `WineCardContent.tsx` (общий компонент `ScanScreen.tsx` и
   `WineCardScreen.tsx` — один код-путь вместо двух копий), запрос по `wine.wine_id` при
   маунте. Рендер: `pairings.length>0` → чипы с `tag` (+ `score`, если не `null` — не
   обязателен визуально, product решает); `pairings=[]` → **показать** `message` текстом
   (не прятать секцию молча — инструкция тимлида п.1: фолбэк «нет данных» обязан быть
   виден, не быть тишиной).
3. «Похоже по вкусу» на экране результата скана (`ScanScreen.tsx`, ветка `confidentCard`):
   вызов `apiClient.analogs({query})` по правилам `contracts/post-scan.md` §2.1 (сначала
   `source.grapes.join(", ")`, при 404 и `grapes.length>1` — повтор с `source.grapes[0]`,
   иначе `source.name`). Компонент результатов — переиспользовать `WineResultChip`
   (уже в `ScanScreen.tsx`, форма как у `result.analogs`). 404 на всех попытках → показать
   `message` из ответа `/analogs` (уже человекочитаем, содержит топ-5 стилей) — не общая
   ошибка/тост.
4. CTA «Пройти вкусовой паспорт» на том же экране → переход на существующий экран
   свайп-дегустации. Гостю (нет consent profiling, `taste/*` вернёт 403
   `consent_required`) — CTA ведёт на регистрацию/апгрейд токена, НЕ показывает 403 как
   ошибку экрана.
5. Опционально (не критерий приёмки): если `style.slug` из п.3 есть в `top_styles`
   (`GET /v1/taste/profile`, только для не-гостя) — пометка «в вашем вкусе».
6. i18n-ключи в `apps/web/src/i18n/{ru,en}.ts` по образцу существующих `wineCard.*`/
   `scan.*` — тексты фолбэков и заголовков блоков (копирайтинг — ваш выбор/product).

**Тесты (vitest, паттерн — `WineCardContent.test.tsx`/`ScanScreen` тесты):**
- гастропары рендерятся при непустом `pairings` (мок ответа с `basis=sensory`).
- `pairings=[]` → виден `message`, не пустая тишина, не бесконечный спиннер.
- «похоже по вкусу»: успех `/analogs` → результаты видны; 404 → виден `message` из
  ответа, не generic-ошибка.
- гость + 403 на `/taste/profile` → CTA на регистрацию, не сломанный экран/красная
  ошибка.
- существующие тесты (`WineCardContent.test.tsx`, `ScanScreen`) остаются зелёными.
- `cd apps/web && npm test` — зелёный.

**Приёмка:** оба новых блока показывают явное состояние на все три случая — есть
данные / нет данных / ошибка сети — ни один не остаётся молчаливым или вечным
спиннером.

---

## Риски / вопросы тимлиду

1. **Владелец `pipeline/ref/`** не назначен в `TEAM.md` (нет такой строки в таблице
   зон) — до сих пор работало по умолчанию (product/data писал, backend читал), но
   формально это дыра. Прошу решить: закрепить за product или считать частью зоны
   backend явной строкой в `TEAM.md`.
2. Таблица дефолтов уровня heuristic и дефолт `abv_percent=12.5` — синтетические
   стартовые точки (как `CV_ABS_FLOOR` в `image-scan.md` до калибровки), посчитаны от
   среднего по 146 эталонным стилям, НЕ от реальных вин каталога кейса (у него нет
   сенсорики, сверить не с чем). Если после демо гастропары идут в прод — нужна
   калибровка на реальной обратной связи, не блокирует эту волну.
3. `portal_tag_defaults` — 9 тегов, матрица блюд неполная (`pipeline/README.md`
   строки 18/141) — product-задача расширения, отдельная от этого контракта.

## Как проверить контракт

```bash
packages/rag/.venv/bin/python3 -c "import yaml; yaml.safe_load(open('contracts/openapi.yaml', encoding='utf-8'))"
grep -n "wine_id}/pairings" contracts/openapi.yaml
```
