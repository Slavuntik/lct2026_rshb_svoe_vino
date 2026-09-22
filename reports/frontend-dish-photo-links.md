# Frontend · «Что подать» по фото блюда + правило ссылок (22.09)

Задача тимлида 22.09 (решение Вячеслава), п.1 и п.2. Контракт `post-scan.md` v1.1 §4/§5 и
`openapi.yaml` v0.3.4 были ратифицированы architect ВО ВРЕМЯ этой реализации — начал по
буквальной схеме из брифа, на середине обнаружил ратификацию (`git status` внезапно показал
чужие изменения в `contracts/`), сверил и переделал типы/моки под неё. Бэкенда живого нет —
`apps/api/app/routers/pairing.py` тоже пишется параллельно (не моя зона, read-only).

## Сделано

1. `apiClient.pairingDishPhoto/pairingDish` + типы `DishInfo/PairingWineItem/DishPairingResponse/
   DishPairingPayload` в `apiTypes.ts` — имена зеркалят схемы `openapi.yaml` 0.3.4 буквально.
2. `ScanScreen.tsx`: переключатель «Бутылка | Блюдо» (chip, как режим в ChatScreen), общая
   камера/загрузка. Статусы: `food` — имя/категория/чипы alternatives/вина-карточки;
   `not_food` — честное сообщение; `bottle` — CTA «Похоже на бутылку — отсканировать?»,
   шлёт ТО ЖЕ `File` в `/v1/scan/photo` (переиспользует готовый bottle-сценарий);
   `unsure` — чипы alternatives, если модель дала слабую догадку, иначе все 9 категорий.
   Ручной выбор категории без фото — отдельная всегда видимая секция в режиме «Блюдо».
   `alternatives` — ДРУГИЕ теги категории (контракт §4.1), не альтернативные названия блюда —
   поэтому чип шлёт `{category}` без `dish`.
3. Правило ссылок (п.2): `ChatScreen.tsx::CitationBadge` — цитата с `wine_id` теперь кнопка,
   внутренняя навигация на `/app/wine/:wineId` (не `href`); `chunk_id`-цитата (статья) — как
   раньше, внешняя ссылка. Аудит остальных списков (аналоги `/analogs`, кандидаты скана,
   гастропары) — уже были internal-first, правок не потребовалось; это совпало с независимым
   аудитом architect в шапке `openapi.yaml` (нашёл то же самое нарушение и то же "уже ок").
4. Мок: `mocks/fixtures/dishPairing.ts` (9 категорий = `portal_tag_defaults`, из `ru.ts`, не
   задублированы), `/pairing/dish` в моке отдаёт 400 `validation_error` на категории вне 9
   (контракт), не молчаливый `unsure`.

## Известное расхождение (не чиним, backend/architect зона)

`apps/api/app/schemas.py::DishInfo.name` — `str = ""`, а ратифицированный `openapi.yaml`
требует `nullable: true`; аналогично `winery/color/sugar` — `str | None` у backend против
required non-null в контракте. Backend сам это пометил в докстрайте роутера как «расхождения
с брифом» (писал раньше ратификации). Типы фронта — строго по контракту; рендер защищён от
ОБОИХ вариантов (`foodDish.name || foodDish.category`, `wine.color && …`) — не всплывёт ни
пустым текстом, ни крэшем, каким бы путём backend это ни причесал.

## Тесты и сборка

`cd apps/web && npm test && npm run typecheck && npm run build` — 17 файлов / **99 тестов**
зелёные (было 84), typecheck/build чистые, Node 24.18.0. Новое: 12 тестов на dish-photo/dish
(все 4 статуса, alternatives, unsure-с-догадкой/без, ручная категория, bottle-мост, ошибка
сети), 2 на правило ссылок в чате (цитата-вино → внутренняя карточка без `source_link_clicked`;
цитата-статья — внешняя ссылка как раньше), 2 apiClient-smoke, 1 node-multipart транспорт.

## Живая проверка

Поднял `vite dev` (порт 5174, 5173 занят), браузер-панель: переключатель и ручной выбор
категории отрисовались корректно (скриншот). Дальше упёрся в инфраструктурный лимит самой
браузер-панели: MSW `Service Worker` не регистрируется («unknown error occurred when fetching
the script») — 502 даже на давно существующем `/v1/events`, значит это не моя регрессия, а
среда просмотра (тот же класс проблемы уже фиксировал `reports/frontend-post-scan.md`).
Логика полностью покрыта детерминированными vitest через `vi.spyOn(apiClient, …)`.

## Вопросы тимлиду

1. `apps/api/app/schemas.py` разошёлся с ратифицированным `openapi.yaml` (name/winery/color/
   sugar nullability) — стоит явно поручить backend сверку, я не трогал (не моя зона).

## Обновление (22.09, доп-задача тимлида: wine_id в первом запросе чата)

Причина: префилл «Спросить сомелье об этом вине» резолвился текстовым поиском только в 91.8%
случаев (вина-близнецы из одной серии) — с `wine_id` сомелье гарантированно говорит про
отсканированное/открытое вино. Контракт (`openapi` 0.3.5) architect оформляет параллельно.

1. `ChatPayload.wine_id?: string` в `apiTypes.ts`. `ChatScreen.tsx`: `location.state.wineId`
   читается в `initialWineIdRef` при монтировании и консьюмится РОВНО один раз, в первом
   вызове `handleAsk` (до `await apiClient.chat`, а не после — сбой первого запроса не должен
   "вернуть" wine_id второму); переключение в режим «Аналог импортного» и обратно ref не трогает
   (это не `/v1/chat`-запрос) — слаг остаётся для настоящего первого вопроса.
2. `ScanScreen.tsx::handleAskSomelierAboutResult` и `WineCardScreen.tsx::handleAskSomelier`
   добавляют `wineId: <card.wine_id>` в `navigate(..., {state})` рядом с `prefillMessage`; нет
   карточки — `if (!result?.card) return` / `if (!wine) return`, как и раньше, значит и
   `wineId` в этом случае просто не будет.
3. Мок: `mocks/fixtures/chat.ts::pickChatResponseForWine(wineId)` — детерминированный ответ
   ИМЕННО про это вино (описание + цитата на его `wine_id`), в обход `pickChatResponse(text)`
   по ключевым словам; `handlers.ts` пробует его первым, если `wine_id` пришёл и резолвится,
   иначе — прежний текстовый разбор (`(body.wine_id && pickChatResponseForWine(...)) ||
   pickChatResponse(body.message)`).
4. Тесты (+5, было 99 → **104**): по одному интеграционному на каждый экран (клик «Спросить
   сомелье…» на реальном `ScanScreen`/`WineCardScreen` → реальный `ChatScreen` на `/app/chat` →
   первый `apiClient.chat` несёт нужный `wine_id`); в `ChatScreen.test.tsx` — обычный чат без
   перехода от вина шлёт запрос с `wine_id=undefined`, второй вопрос диалога уже без слага
   первого, и переключение в «Аналог импортного» и обратно не расходует его впустую.
5. `cd apps/web && npm test && npm run typecheck && npm run build` — 17 файлов / 104 теста,
   typecheck/build зелёные, Node 24.18.0. Коммит отдельный, с pathspec.
