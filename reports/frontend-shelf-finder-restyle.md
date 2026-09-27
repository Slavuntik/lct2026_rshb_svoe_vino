# Frontend — «Витрина» (apps/shelf-finder) в дизайн-системе приложения (27.09)

Дополнение интеграции: используются общие исходники apps/web и локальные шрифты.
Правки статусов, danger, disabled и размеров заголовков из обоих раундов сохранены;
подробности — [единая система](../docs/product/design-system.md). Ниже исторический отчёт.

Перенёс палитру/шрифты/радиусы/отступы apps/web (tokens.css) на `apps/shelf-finder`, затем внёс
4 правки по ревью тимлида (раунд 2, см. ниже). Правки: `index.html`, `src/style.css`, точечно
`src/main.ts`, новый `src/tokens.css` (копия светлого блока `apps/web/src/styles/tokens.css` +
локальные добавления, см. «Предложение архитектору»). apps/web не менял. Логику/камеру/
`engine=`/`core.ts`/`geometry.ts`/`preprocess.ts`/`worker.ts` не трогал.

## Было → стало
| Параметр | Было | Стало |
|---|---|---|
| Акцент/CTA | `#123c32` зелёный | `--accent #AB494F`, hover `--accent-strong #7B3528` |
| Второй акцент | `#b99152` золото | убран, акцентные точки — `--accent` |
| Текст / приглушённый | `#203830` / `#6a7870` | `--ink #2C2A28` / `--muted #6B6A68` |
| Линии/чипы, фон/карточки | `#d8dfd6` / `#f5f4ef`,`#fffefa` | `--line`,`--mono-bg #E3E1E2` / `--bg #FEFDFA`,`--card #FDF9ED` |
| Успех / ошибка | `#99b898`,`#225336` / `#e0bbae`,`#703c2c` | `--ok #3E6B4F` / `--danger #7B3528` на `--danger-bg` (раунд 2, было `--warn` — см. ниже) |
| Заголовок страницы / секции | Georgia, вразнобой 20–28px | `h1`=`--fs-h1`(clamp 34–48) / `h2`=`--fs-h2-card` 22px (раунд 2, было единое `--fs-h2` 32px) |
| Текст/кнопки | `Inter` в CSS, но не грузился шрифт | `Inter` реально подключён (Google Fonts, приём apps/web) |
| Радиус кнопки/поле/карточка/пилюля | 8 / 5–7 / 14–16 / 30px | `--radius-sm 14` / `--radius-sm 14` / `--radius 20` / `--radius-pill 999` |
| Точка статуса | всегда зелёная | `--muted`(ожидание)/`--ok`(готово)/`--danger`(ошибка) — раунд 2 |
| Disabled-кнопка | `opacity:.45` (белый на бордовом → 1.99:1) | непрозрачная `--mono-bg`/`--ink` → 10.98:1 — раунд 2 |
| Фокус-кольцо, `theme-color` | `#a87e30` 3px / `#123c32` | `--accent` 2px (как global.css) / `#AB494F` |

Приёмы из `global.css`: `.card`→`aside`; `.badge`→`.private`; `.badge--ok`→`.result.found`;
`.field__input`→поля/`select`; `.modal`/`.modal-overlay` (скрим `rgb(43 34 38/60%)`)→`dialog`.

## Раунд 2 — правки по ревью тимлида
1. **Точка статуса.** `main.ts`: добавил `updateDot()` — читает уже существующие в коде
   состояния (`ready`, `busy`, видимость `#error`), новых не вводил; вызывается из `status()`/
   `error()` (обе и так дергаются на каждом переходе — проверил все места присвоения `busy`/
   `ready`). `data-state` = `error` (приоритет) → `waiting` (`!ready||busy`) → `ok`. CSS:
   `.dot[data-state=ok]`=`--ok`, `=error`=`--danger`, по умолчанию `--muted`.
2. **`.error` был на `--warn`.** Завёл `--danger:var(--accent-strong)`/`--danger-bg:var(
   --accent-soft)` в `tokens.css` (локально, не в общем контракте) — на основе акцента, как
   просили, но текстом взял `--accent-strong`, а не голый `--accent`: пара `--accent/
   --accent-soft` даёт 4.56:1 (впритык), `--accent-strong/--accent-soft` — 7.27:1. Оба варианта
   ≥4.5, взял с большим запасом для текста ошибки. Предложение архитектору — следующим пунктом.
3. **Заголовки карточек.** Добавил `--fs-h2-card:22px` (локально), `h2{font-size:...}` теперь
   на нём, а не на `--fs-h2`(32, который остаётся токеном заголовка страницы). Проверил
   `getBoundingClientRect`: "Соберите свой выбор" — 1 строка (24px высоты = 1 line-height).
   Заодно нашёл и починил соседний дефект той же природы: `.selection` ("Выбрано: 0" +
   2 ссылки) на брейкпоинте 900px (aside сужается до 290px) переносило одну из двух ссылок
   саму по себе из-за `justify-content:space-between` на 3 элементах — обернул обе ссылки в
   `.selection__actions`, теперь при нехватке места переносится вся пара целиком, не полурвано
   (375/1280 — как и было, в одну строку; 900px — теперь чисто в 2). Классов/ID тестов не
   касался, `.selection__actions` — новый, не используется в `tests/browser/*.spec.ts`.
4. **Disabled-кнопки.** `opacity:.45` гасило ЦЕЛИКОМ и заливку и текст к фону страницы —
   посчитал эффективные цвета (WCAG-формула после альфа-блендинга): белый на `--accent` при
   45% → `#FEFEFC` на `#D9ACAD` → **1.99:1**, хуже активной кнопки. Заменил на непрозрачную
   `background:var(--mono-bg);color:var(--ink)` (тот же приём и для `.button:has(input:
   disabled)`) → **10.98:1**. `.quiet:disabled` (без заливки) — гашу только текст до `--muted`
   (5.31/5.13 на bg/card), плашку не добавляю — она была бы новой заливкой там, где обычно фона нет.

## Предложение архитектору (на ратификацию)
В общем `tokens.css` нет пары под состояние "ошибка/отказ" (только `--ok`/`--warn`) — в
Figma-макетах такого состояния не было вовсе. Красить сетевой отказ в `--warn` (жёлто-охристый,
предупреждение) семантически неверно: это разные категории. Предлагаю добавить в контракт
`--danger`/`--danger-bg` на основе уже принятого `--accent-strong`/`--accent-soft` (7.27:1,
новый оттенок не потребуется). Также параллельно в apps/web вводят отдельный уровень «заголовок
карточки» (по словам тимлида, ~22–24px) — у себя завёл `--fs-h2-card:22px` тем же способом
(локально, до ратификации); если архитектор утвердит другое число или имя — поменять централизованно.

## Контраст (WCAG-формула, скрипт `.../scratchpad/shelf-restyle/contrast.py`)
ink/bg 14.05, ink/card 13.58, muted/bg 5.31, muted/card 5.13, ink/mono-bg (`.private`,
disabled-кнопки) 10.98, on-accent/accent 5.53, accent/accent-soft (`.secondary`, активная) 4.56,
accent/bg и accent/card (`.quiet`,`summary`) 5.44/5.25, **danger(accent-strong)/danger-bg
(`.error`, точка) 7.27** (было `--warn/--warn-bg` 5.18, семантически неверно — заменено),
ok/ok-bg (`.result.found`) 5.30, ink-тёмный/градиент стейджа 12.7–14.5, muted-тёмный/градиент
5.0–5.7. **Disabled-кнопка: было 1.99 (эффективный цвет при opacity:.45) → стало 10.98.** Все
использованные пары ≥4.5:1.

## Вне палитры — намеренно
`.stage`/`.empty` (видоискатель до кадра) — буквально ветка `:root[data-theme="dark"]` из
tokens.css (не новая тёмная тема: не переключается, было тёмным и раньше). Рамка найденной
бутылки на canvas (`main.ts`) — `#25cf6933`/`#9df5ad` вне палитры как разметка поверх
произвольного кадра (тот же принцип, что `.aim__frame`); подпись-плашку перевёл с хардкода
`#123c32` на `getComputedStyle(--ink)` в рантайме.

## Не менял
Классы/ID `.result`, `.empty`, `#error/#status/#catalog/#search/#stop/#enroll/#photo/#frame`
— на них `tests/browser/*.spec.ts` (прогнаны, см. ниже, зелёные). Добавленные `id="dot"` и
`.selection__actions` — новые, ничего существующего не переименовывал. Крупную раскладку
(max-width 1360, aside 335px, паддинги) не трогал. `dialog` max-width оставил 480, не 420 как
`.modal`.

## Чем проверял
`npm run test` 24/24 зелёных (оба раунда). `npm run build` чисто, CSS 9.28 КБ.
**`npx playwright test` прогнан по требованию тимлида: 7 passed, 3 skipped, 0 failed** (6.3с).
Пропущены штатно, не из-за моих правок — все три через `test.skip(!photo || …, 'Set
SHELF_TEST_PHOTO…'/'Set SHELF_SERVER_TEST_PHOTO…')` в самих спеках (`shelf.spec.ts:11,48`,
`server-live.spec.ts:6`), требуют локального фото-фикстура/живого сервера через переменные
окружения — их нет в этом окружении и не было бы независимо от моих изменений. Из зелёных:
`shelf.spec.ts:4` ("missing models show an actionable error…") напрямую бьёт по `#error`/
`.result`, `server.spec.ts` (6 тестов) — по канвасу/дублям/EXIF/таймауту; всё прошло с новой
разметкой (`id="dot"`, `.selection__actions`). Уточнение по среде: `chromium-headless-shell`
в кэше был ревизии 1234, этому `playwright-core` 1.63.0 нужна 1243 — реально скачал 94.3 МБ
(`npx playwright install chromium-headless-shell`, ~4 мин, один обрыв соединения и ретрай) —
это не «ничего качать не надо», а несовпадение версии в общем кэше `~/Library/Caches/
ms-playwright` с другим проектом; полный `chromium-1234` (не headless-shell) как был, так и
остался нетронут и рабочим.
Смотрел живьём: `npm run dev` 375/1280, `?embedded=1` и без, оба раунда правок; отдельно
`vite build`+`vite preview` внутри iframe тестовой страницы 375px (`tools/run_local.py` не
поднимал — задел бы параллельных агентов/секреты шлюза).

Скриншоты — `.../scratchpad/shelf-restyle/`: раунд 1 `before/after-{desktop,mobile-375}
[-embedded].png`, `after-results-states.png`, `after-dialog.png`, `after-embed-in-iframe-375.png`;
раунд 2 `after2-desktop-error.png`, `after2-mobile-375-error.png` (точка+`.error` в реальном
отказе сервера), `after2-disabled-zoom.png`, `after2-dot-and-error-zoom.png`,
`after2-result-header-zoom.png`.

## Раунд 3 — ратификация v0.2, чужой мёрдж, верификация (без правок кода)

Архитектор ратифицировал `--danger`/`--danger-bg` (=`--accent-strong`/`--accent-soft`, как и
предлагал) и переименовал `--fs-h2-card` → **`--fs-h2-sm`** (contracts/tokens.css v0.2,
reports/architect-tokens-v02.md). Пока применял переименование, в `main` прилетел мёрдж чужого
коммита `dfb9302`, который переписал `apps/shelf-finder/src/{tokens.css,style.css,main.ts}` на
более чистую архитектуру: `tokens.css` — не копия, а `@import "../../web/src/styles/tokens.css"`
(«копий не расходятся, потому что копии нет»), `main.ts` импортирует `apps/web/src/styles/
global.css` напрямую и рантайм-функцией `styleButtons()` навешивает `.btn`/`.btn--*` на все
кнопки. Мои содержательные правки (состояния точки, `--danger`, `--fs-h2-sm`, disabled-контраст)
сохранены внутри этой новой архитектуры.

**Моя ошибка и откат.** Не увидев сразу этот мёрдж, я один раз переписал `tokens.css` обратно в
полную копию (коммит `bd3af96`) — ровно та переделка, от которой тимлид просил воздержаться.
Отменено коммитом `04c439c` тем же приёмом (`git checkout <мёрдж> -- tokens.css`), файл вернулся
байт-в-байт к состоянию после `dfb9302`. `git stash` с моей старой версией не поднимал.

**Верификация чужого мёрджа (все проверки — по вычисленным значениям, Playwright, не на глаз):**
- Точка статуса: `data-state` waiting→error при отказе сервера, `backgroundColor` = `rgb(123, 53,
  40)` = `--danger` — во всех 4 комбинациях (standalone/`?embedded=1` × 375/1280).
- `.error`: `background rgb(243,230,233)` / `color`+`border rgb(123,53,40)` — контраст 7.27:1.
- Заголовки: `aside h2`, `.result-header h2`, `.empty h2`, диалог `#enroll h2` — все 22px,
  высота 24px = 1 строка (line-height 24.2px), нигде не переносится на 2 строки.
- Disabled-кнопка (`#camera` до `ready`): `opacity:1`, `background rgb(227,225,226)`/`color
  rgb(44,42,40)` — контраст **10.98:1**.
- Тёмная ОС (`colorScheme:'dark'` в Playwright, 375 и 1280): вычисленный `getComputedStyle(html).
  colorScheme` = **"light"**, фон страницы `rgb(254,253,250)` (светлый, не тёмный). Открыл
  `<select id="recognition-mode">` и заскриншотил — рендерится светлым (белый фон, тёмная
  стрелка), нативного тёмного виджета нет (скриншот `verify3-select-dark-full.png`).
- `npm run test` — 24/24. `npx playwright test` (полный набор, включая новые спеки второго
  разработчика) — **8 passed, 5 skipped, 0 failed**; все skip — штатный `test.skip` по
  отсутствующим `SHELF_TEST_PHOTO`/`SHELF_SERVER_TEST_PHOTO`/`SHELF_MAIN_UI_URL`, не мои правки.
  Отдельно отмечу `server.spec.ts:105` («standalone shelf uses main tokens, local fonts and
  touch controls», зелёный) — новый тест второго разработчика уже проверяет то же самое
  (тёмная ОС, фон `rgb(254,253,250)`, шрифты Inter/Playfair Display, `#camera` `rgb(171,73,79)`,
  44px тап-зона) машинно и на CI, не только у меня руками.

**Вывод: всё цело.** Ни одна из четырёх моих правок по ревью не откатилась и не сломалась.
`apps/shelf-finder/src/tokens.css` — единственный файл, который я трогал в этом раунде (откат
своей же ошибки); `style.css`/`main.ts` не мои — их полностью переписал `dfb9302`, и они рабочие.

Новые скриншоты — `.../scratchpad/shelf-restyle/`: `verify3-{standalone,embedded}-{375,1280}.png`,
`verify3-dark-{375,1280}.png`, `verify3-select-dark-full.png`.

## Риски / тимлиду
1. `--fs-h2-card`/`--danger` в старой формулировке этого отчёта (раунды 1–2) — устарели, читать
   раунд 3 и `reports/architect-tokens-v02.md` как источник истины по именам/значениям.
2. `contracts/check_tokens.py` в текущем виде не понимает `@import` и после `dfb9302` продолжит
   рапортовать `apps/shelf-finder/src/tokens.css: нет блока :root`, если его прогнать буквально —
   это ожидаемо при выбранной архитектуре («копий не расходятся, потому что копии нет»), не
   регрессия; чинить скрипт или контракт — решение архитектора/тимлида, не моя зона.
