# Frontend · пост-скан — гастропары + «Похоже по вкусу» (22.09)

Бриф: `reports/architect-post-scan.md` → «Задача frontend». Контракт: `contracts/post-scan.md` v1.0.
Backend-ручка `GET /wines/{id}/pairings` делалась параллельно — работал по контракту, свой мок
(`mocks/handlers.ts`) + прямой `vi.spyOn` в юнит-тестах, `/v1/analogs` и `/v1/taste/*` не трогал.

## Сделано

1. `apiClient.getWinePairings(wineId)` + типы `WinePairingsResponse/WinePairing/WinePairingsBasis`
   в `apiTypes.ts` (зеркалят `openapi.yaml` `/wines/{wine_id}/pairings`).
2. Блок «К чему подать» — внутри `WineCardContent.tsx` (`WinePairingsBlock`, один код-путь для
   `WineCardScreen` и инлайн-карточки скана), запрос по `wine.wine_id` при маунте. Три видимых
   состояния: загрузка → чипы тегов с честной подписью источника (`pairingsSourceCatalog/
   Sensory/Heuristic`, basis=unavailable подписи не несёт) **или** текст `message` (пусто ⟺
   pairings=[], контракт) **или** сетевая ошибка (`common.errorGeneric`) — не тишина, не вечный
   спиннер.
3. «Похоже по вкусу» на `ScanScreen.tsx` (ветка `confidentCard`): `POST /v1/analogs` по
   `source.grapes.join(", ")`, при 404 и >1 сорте — повтор по `grapes[0]`, иначе `source.name`.
   Успех → стиль + `WineResultChip`; 404 → `message` из ответа (топ-5 стилей, не generic);
   прочая ошибка → `common.errorGeneric`. Шлёт существующее событие `analog_requested`.
4. CTA «Пройти вкусовой паспорт»: гость (`storage.getAccountKind()==="guest"`) → `/app/profile`
   (апгрейд токена, без обращения к `/taste/*` — не тратим запрос на заведомый 403); иначе →
   `/app/taste`.
5. Опционально: если `style.slug` есть в `top_styles` (`GET /v1/taste/profile`, только не-гость,
   все ошибки тихо игнорируются) — бейдж «В вашем вкусе».
6. i18n: 5 ключей `wineCard.pairings*`, 5 ключей `scan.tasteAnalogs*`/`tastePassportCta` в `ru.ts`.
   Мок `GET /wines/:wineId/pairings` в `handlers.ts` — basis=catalog из `food_pairings` (все
   фикстуры его несут) либо unavailable; sensory/heuristic проверены юнит-тестами напрямую.

## Тесты и сборка

`cd apps/web && npm test` — 17 файлов / 82 теста зелёные (было ~75, +9 новых: 3 на пейрингах,
6 на «Похоже по вкусу»/CTA); `npm run typecheck` и `npm run build` — зелёные, Node 24.18.0.
2 существующих теста (`ScanScreen.test.tsx`: «уверенный матч», «drag-and-drop») дополнены
`await waitForTasteAnalogsSettled()`, чтобы фоновые fetch'и оседали до конца теста (иначе
шумели бы act()-варнингом после `cleanup()`).

## Обновление (решение тимлида, после первой сдачи)

Дублирование при basis=catalog (раньше было риском в этом отчёте) — тимлид сообщением снял
запрет архитектора «новый эндпоинт не подменяет этот рендер» и велел убрать старую секцию.
Сделано: статический блок «Сочетания» (`source.food_pairings` напрямую) и i18n-ключ
`wineCard.foodPairingsLabel` удалены — «К чему подать» теперь единственный рендер (при
basis=catalog — тот же список с подписью «По данным карточки вина»). Обновлён 1
regression-тест (был синхронным на секции «Сочетания» — теперь ждёт `wine-pairings-block`).
`contracts/post-scan.md` §1 текстуально не трогал (не моя зона) — строка там устарела, поправить
архитектору при случае. vitest/typecheck/build перепрогнаны зелёными, коммит отдельный, с pathspec.

## Риски / предложения к контракту

1. Скор гастропар (`pairing.score`) в UI не показываю (не обязателен, product решает) — как
   top1_score/gap/score кандидатов, нигде не рендерим сырые числа. `triggered_rules.explain`
   тоже пока не в UI (только в типе) — кандидат на тултип отдельной итерацией.

## Как проверить

```bash
cd apps/web && npm test && npm run typecheck && npm run build
```
