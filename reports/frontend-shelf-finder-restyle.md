# Frontend — «Витрина» (apps/shelf-finder) в дизайн-системе приложения (27.09)

Перенёс палитру/шрифты/радиусы/отступы apps/web (tokens.css) на `apps/shelf-finder`. Правки:
`index.html`, `src/style.css`, точечно `src/main.ts` (см. ниже), новый `src/tokens.css`
(копия светлого блока `apps/web/src/styles/tokens.css`, шапка с источником; тёмную ветку не
взял — у «Витрины» не было тёмной темы, заводить без макета запрещено заданием). apps/web не
менял. Логику/камеру/`engine=`/`core.ts`/`geometry.ts`/`preprocess.ts`/`worker.ts` не трогал.

## Было → стало
| Параметр | Было | Стало |
|---|---|---|
| Акцент/CTA | `#123c32` зелёный | `--accent #AB494F`, hover `--accent-strong #7B3528` |
| Второй акцент | `#b99152` золото | убран, акцентные точки — `--accent` |
| Текст / приглушённый | `#203830` / `#6a7870` | `--ink #2C2A28` / `--muted #6B6A68` |
| Линии/чипы, фон/карточки | `#d8dfd6` / `#f5f4ef`,`#fffefa` | `--line`,`--mono-bg #E3E1E2` / `--bg #FEFDFA`,`--card #FDF9ED` |
| Успех / ошибка | `#99b898`,`#225336` / `#e0bbae`,`#703c2c` | `--ok #3E6B4F` / `--warn #845A19` (на `-bg`) |
| Заголовки | Georgia/системный, вразнобой 20–28px | Playfair Display, единый `h1`/`h2` из global.css |
| Текст/кнопки | `Inter` в CSS, но не грузился шрифт | `Inter` реально подключён (Google Fonts, приём apps/web) |
| Радиус кнопки/поле/карточка/пилюля | 8 / 5–7 / 14–16 / 30px | `--radius-sm 14` / `--radius-sm 14` / `--radius 20` / `--radius-pill 999` |
| Фокус-кольцо, `theme-color` | `#a87e30` 3px / `#123c32` | `--accent` 2px (как global.css) / `#AB494F` |

Приёмы из `global.css`: `.card`→`aside`; `.badge`→`.private`; `.badge--ok`→`.result.found`;
`.field__input`→поля/`select`; `.modal`/`.modal-overlay` (скрим `rgb(43 34 38/60%)`)→`dialog`.

## Контраст (WCAG-формула, скрипт `.../scratchpad/shelf-restyle/contrast.py`)
ink/bg 14.05, ink/card 13.58, muted/bg 5.31, muted/card 5.13, ink/mono-bg (`.private`) 10.98
(muted/mono-bg было бы 4.15 — FAIL, поэтому там ink), on-accent/accent 5.53, accent/accent-soft
4.56, accent/bg и accent/card (`.quiet`,`summary`) 5.44/5.25, warn/warn-bg (`.error`) 5.18,
ok/ok-bg (`.result.found`) 5.30, ink-тёмный/градиент стейджа 12.7–14.5, muted-тёмный/градиент
5.0–5.7. Все пары ≥4.5:1.

## Вне палитры — намеренно
`.stage`/`.empty` (видоискатель до кадра) — буквально ветка `:root[data-theme="dark"]` из
tokens.css (не новая тёмная тема: не переключается, было тёмным и раньше). Рамка найденной
бутылки на canvas (`main.ts`) — `#25cf6933`/`#9df5ad` вне палитры как разметка поверх
произвольного кадра (тот же принцип, что `.aim__frame`); подпись-плашку перевёл с хардкода
`#123c32` на `getComputedStyle(--ink)` в рантейме — единственная правка в `main.ts` кроме
добавленной константы.

## Не менял
Классы/ID `.result`, `.empty`, `#error/#status/#catalog/#search/#stop/#enroll/#photo/#frame`
— на них `tests/browser/*.spec.ts`. Крупную раскладку (max-width 1360, aside 335px, паддинги) —
не часть общей шкалы отступов. `dialog` max-width оставил 480, не 420 как `.modal`.

## Чем проверял
`npm run test` 24/24 зелёных. `npm run build` (`tsc --noEmit`+`vite build`) чисто, CSS 8.91 КБ.
`playwright test` не гонял (не качал/не трогал браузеры параллельно с другими агентами) —
скриншоты снимал отдельным процессом через уже установленный `chromium-1234`. Смотрел живьём:
`npm run dev` (5180, был свободен), 375/1280, с `?embedded=1` и без; отдельно `npm run build` +
`vite preview` (4180) внутри iframe тестовой страницы 375px — `tools/run_local.py` не поднимал
(полный стек задел бы параллельных агентов и секреты шлюза). В консоли iframe-проверки только
чужие 500 (`/v1/shelf` без бэкенда) и 404 `favicon.ico` (не было и раньше).

Скриншоты — `.../scratchpad/shelf-restyle/` (полный путь в конце ответа тимлиду): `before/after-
{desktop,mobile-375}[-embedded].png`, `after-results-states.png`, `after-dialog.png`,
`after-embed-in-iframe-375.png`.

## Риски / тимлиду
1. `tests/browser/*.spec.ts` не прогнан (см. выше) — селекторы не менял, но стоит проверить живьём.
2. `accent/accent-soft` 4.56:1 — впритык, пара общая с apps/web, не моя правка.
