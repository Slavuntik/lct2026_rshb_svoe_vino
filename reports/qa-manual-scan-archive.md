# QA manual · Разбор архива реальных сканов со стенда

Дата: 27.09.2026. Источник: `case-data/scan-archive/scans` (вне git, 659 МБ, в репозиторий не кладём —
ниже только имена файлов). Проверено глазами: все 63 уникальных уверенных ответа, все 24 записи
`unsure` (13 уникальных фото) и все 44 уникальных фото `failed` (задание просило 15).

Метод по каждому фото: сначала читаю этикетку сам, затем ищу вино в каталоге кейса
(`case-data/real-photos-labels/search_catalog.py`, при необходимости `--winery`), затем сверяю
дизайн с эталоном и только потом смотрю, что выдал сервис. Инструмент сверки — склейка
«фото + эталон предсказанного слага» (скрипты во временной папке сессии, в репо не кладу).

## Что в архиве

| корзина | записей | уникальных фото | порог |
|---|---|---|---|
| confident | 136 | 63 | score ≥ 0.80 и gap ≥ 0.03 |
| unsure | 24 | 13 (12 своих + 1 общее с confident) | score ≥ 0.80, gap < 0.03 |
| failed | 93 | 45 (44 своих + 1 общее с confident) | score < 0.80 |
| всего | 253 | 119 | — |

Границы измерены по данным: минимальный confident — 0.8010, максимальный failed — 0.8024
(единственный перехлёст, у него gap 0.004). Сбоев в архиве нет ни одного: `failed` — это честное
«нет в каталоге» по порогу.

## Сводка по вердиктам

| корзина | верно | ошибка | близнец | пропуск | верный отказ | не вино / не определить |
|---|---|---|---|---|---|---|
| confident (63) | 57 | 4 | 2 | — | — | — |
| unsure (12 своих) | — | — | — | 4 | 6 | 2 |
| failed (44 своих) | — | — | — | 11 | 30 | 3 |

Уверенная точность по уникальным фото: **57/63 = 90.5 %** строго, **59/63 = 93.7 %** если близнецов
считать попаданием. Четыре ошибки приходятся на три разных вина (одно снято дважды).
Пропусков — 15 (4 в unsure + 11 в failed), это 15 уникальных фото и 14 разных вин
(Табия «Олег» снят дважды). Пропуск для критерия достоверности дороже уверенной ошибки,
поэтому разбирать стоит сначала их.

## Уверенные ошибки — конкретно (для ml-lead)

Пути даны от `case-data/scan-archive/scans/`.

1. **`confident/20260922/20260922T120825_dd15f2.jpg`** и **`confident/20260922/20260922T120922_68b804.jpg`**
   (одно вино, два кадра). На фото — Усадьба Дивноморское, «Терруар. Вторая линия. **Вечерница**»,
   красное. Выдано `usadba-divnomorskoe-solnechnyy-veter-shardone-beloe-suhoe-125` («Солнечный Ветер»,
   Шардоне, белое), score 0.870 / 0.875. В каталоге у винодельни 17 вин, «Вечерницы» среди них нет —
   правильный ответ «нет в каталоге». Вся серия «Вторая линия» имеет одинаковую этикетку (краб и
   раковина), различается только строка с названием внизу. То же вино на двух других кадрах ушло в
   `unsure` (`unsure/20260922/20260922T120955_06e33e.jpg`, `.../20260922T121210_e0ec03.jpg`) —
   значит, разброс по кадру больше, чем зазор до порога.
2. **`confident/20260922/20260922T121144_3afcd8.jpg`**. На фото — Golubitskoe Estate «Winery Series
   **RED BLEND**», красное. Выдано `golubitskoe-estate-winery-series-roze-blend-...-117` («Розе бленд»,
   розовое), score 0.823. Цвет вина и цвет ответа противоположны — дешёвый контроль по цвету жидкости
   поймал бы это. В каталоге «Winery Series Red Blend» нет; ближайшее — `golubitskoe-estate-red-blend-...-136`
   с другой этикеткой.
3. **`confident/20260927/20260927T012421_042690.jpg`**. В кадре бутылки нет вообще: фасад «Семейной
   винодельни Литавщуков» (табличка на стене), кадр 1034×556. Выдано `lesnaya-proseka`
   («Кубань-Вино. Лесная просека»), score 0.853, gap 0.036. Это ровно тот случай, под который
   ml-engineer завёл вето «не бутылка» (56dfd2d) — надо проверить, что этот кадр им перекрывается.

Близнецы (ошибка дешевле, но top-1 всё равно не тот):

4. **`confident/20260922/20260922T121150_aba69d.jpg`** — на фото Табия «**Цитронный Магарача**» (красный
   ферзь), выдан слаг `czitronnyj-magaracha`, который в каталоге называется «Цитрон» и показан
   оранжевым конём, т. е. это соседняя бутылка той же серии. Отдельного «Цитронного Магарача» у Табии
   в каталоге нет (всего 9 вин). Спорно: слаг буквально называется как вино на фото.
5. **`confident/20260922/20260922T121242_b7d8d2.jpg`** — Массандра «Саперави», вино ординарное сухое,
   урожай 2025, классическая бежевая этикетка. Выдано `caperavi-avtorskoe-vino` («Cаперави. Авторское
   вино») — другая линейка с другой этикеткой. Ординарного Саперави среди 44 вин Массандры нет.

## Пропуски — конкретно (важнее ошибок)

Правильный слаг был **top-1**, но отклонён порогом:

| файл | вино | слаг | score | почему отклонён |
|---|---|---|---|---|
| `failed/20260922/20260922T120403_6cf1f4.jpg` | Табия «Розовое золото» 2024 | `rozovoe-zoloto` | 0.797 | порог 0.80, не хватило 0.003 |
| `failed/20260922/20260922T121216_b2d625.jpg` | Табия «Олег» 2025 | `oleg` | 0.783 | порог |
| `failed/20260922/20260922T121156_bde664.jpg` | Табия «Олег» 2025 | `oleg` | 0.727 | порог |
| `failed/20260922/20260922T121203_1052f7.jpg` | Ведерниковъ Долина Дона белое сухое | `vinodelnya-vedernikov-vedernikov-dolina-dona-beloe-suhoe-aligote-12` | 0.785 | порог |
| `failed/20260922/20260922T121040_1d71cb.jpg` | Новый Свѣтъ выдержанное полусладкое розовое 2023 | `novyy-svet-...-polusladkoe-rozovoe-novyy-svet-shardone-125` | 0.779 | порог |
| `failed/20260922/20260922T121229_d78a92.jpg` | Denisov «Красная стрелка» Рубин | `denisov_rubin_klaret_krasnaya_strelka` | 0.776 | порог; то же вино уверенно узнано на C02 (0.851) |
| `failed/20260922/20260922T120734_20d0c3.jpg` | Inkerman Riesling 2025 Winemaker's Selection | `winemaker-selection` | 0.761 | порог + **у слага нет эталона** |
| `failed/20260922/20260922T164740_7b5451.jpg` | Фанагория Cru Lermont Riesling 2024 | `cru-lermont-risling` | 0.698 | порог + **у слага нет эталона** |
| `failed/20260922/20260922T113254_b9368f.jpg` | Фанагория Velvet Season Muscat | `fanagoriya-velvet-season-muskat-ottonel-beloe-sladkoe-13` | 0.689 | порог + **эталон отклонён при ревью** |
| `failed/20260922/20260922T154522_5fcb22.jpg` | Di Caspico Fiori di Mare Verde | `di-kaspiko-di-caspico-fiori-di-mare-verde` | 0.688 | порог; кадр — полка, вино по центру |
| `unsure/20260926/20260926T183138_61ed14.jpg` | Abrau-Durso Reserve Brut | `abrau-dyurso-abrau-durso-reserve-brut-shardone-beloe-bryut-115` | 0.946 | gap 0.006 против Brut Rose Reserve |
| `unsure/20260923/20260923T145321_62848b.jpg` | Галицкий и Галицкий Каберне Совиньон | `kaberne-sovinon-1` | 0.918 | gap 0.008 против Appassimento |
| `unsure/20260922/20260922T120831_abbc9a.jpg` | Golubitskoe Estate Cabernet Sauvignon 2023 | `golubitskoe-estate-kaberne-sovinon-krasnoe-suhoe-137` | 0.877 | gap 0.012 |

Правильный слаг был **не** top-1:

- `unsure/20260923/20260923T145618_f56661.jpg` — Галицкий и Галицкий Каберне Совиньон (фото экрана
  с карточкой товара). Top-1 — `kaberne-sovinon-appasimento`, правильный `kaberne-sovinon-1` на 2-м
  месте (0.913 против 0.910). Отказ здесь спас от уверенной ошибки.
- `failed/20260922/20260922T120422_045363.jpg` — «Семейная винодельня Литавщуков», Мерло сухое.
  В каталоге есть `merlo-litavshhuk`, но его нет даже в top-5: top-1 — «Союз-Вино Кубанское
  Традиционное». Здесь CV промахнулся содержательно, а не по порогу.

Вывод по порогу: **6 из 15 пропусков** — это score 0.69–0.80, т. е. просто недобор до floor, и ещё
3 — margin 0.006–0.012 между близнецами одной линейки. Три пропуска (Velvet Season Muscat,
Winemaker selection, Cru Lermont Рислинг) невозможно закрыть порогом вообще: у этих слагов в
`slug_refs.json` нет пригодного эталона (`chosen: null`), таких слагов в каталоге **44**.

## Странное в архиве

- **Одно вино на разных фото** — типичная картина стенда: «Вечерница» снята 4 раза (2 confident-ошибки
  + 2 unsure), Фанагория «Зелёное вино» — 4 раза, Denisov Совиньон Блан — 4, ALVEUS «Оранж брют» — 5,
  Литавщуков Совиньон Блан — 4, Шато Пино «Беленькое» — 3, Табия Розе — 3, Рубин Голодриги — 3.
  Ни на одном повторе ответ не менялся — меняется только корзина.
- **Одно фото в двух корзинах (2 случая).** `20260921T094736_65779c` (Массандра Мускатель) —
  21.09 confident 0.839 на индексе `case-20260918`, 22.09 и 27.09 failed 0.786 на `case-20260921-d1-b384`.
  `20260922T120555_7dceeb` / `20260927T002849_4db04a` (Дэсоно Каберне) — unsure 22.09 → confident 27.09
  при том же score 0.870. Ответ один, корзина разная: индекс/порог поменялись между прогонами.
- **Кадры не про вино — 5 штук.** Здание винодельни (`confident/20260927/20260927T012421_042690.jpg`,
  уверенно ошибся), пейзаж с озером (`unsure/20260927/20260927T012340_dcc246.jpg`, score 0.837 — почти
  прошёл) и три стеллажа магазина целиком (`failed/.../20260922T154458_1fb059.jpg`,
  `failed/20260925/20260925T153243_8f68ee.jpg`, `.../20260925T155158_a38e40.jpg`).
- **Не живые фото, а картинки из интернета — 9 кадров.** Каталожные рендеры на белом/чёрном
  (203×630, 118×500, 102×500, 176×630, 540×540 ×2, 1200×1200), фото экрана с карточкой товара и
  обрезанный кроп 1034×556. Самый мелкий — 102×500: этикетка нечитаема даже человеком. Это, видимо,
  проверки «на кошках», но они дают 5 повторов на одном рендере (Фанагория «Она сказала Да!») и
  перекашивают статистику по корзинам.
- **Полки и ценники** в кадре почти везде, где съёмка в магазине; центральная бутылка при этом
  определяется однозначно — сервис на этих кадрах не путает соседей ни разу, кроме C56.

## Дефекты каталога (не чиню, передаю)

- **Дубли слагов на одно вино** — прямой риск для top-1 на приватной проверке: у АРАТТИ три слага
  «Жемчужная 9 Пино Нуар, Мускат Розовый» (`...-rozovyj`, `-1`, `-2`), два «Жемчужная 9 Цитрон,
  Шардоне», два «Каберне по-белому» (один помечен Красное, второй Розовое), два «Жемчужная 9 Каберне
  Совиньон, Пино Нуар». У Фанагории «Formula Q» и «Декантер. Формула Q» — один и тот же состав сортов.
  Восемь уверенных ответов архива попали в такие семьи; в названиях каталога уровень сахара не указан,
  поэтому выбрать «правильный» дубль нельзя в принципе.
- **`uva-vallis-risling`** — Uva Vallis «levitas» Riesling, белое вино, в каталоге `color: Красное`.
- **`case-data/thumbs/bukovinka.webp`** — не бутылка, а плакат «Российский винодельческий форум».
  В `slug_refs.json` эталон уже заменён на нормальный кадр, но в `thumbs/` лежит старая картинка и
  вводит в заблуждение при ручной разметке.
- **`case-data/thumbs/fanagoriya-velvet-season-*.webp`** — у обоих белых Velvet Season (Мускат Оттонель
  и Рислинг) в превью стоят красные бутылки Saperavi и Cabernet. В `slug_refs.json` мускатный эталон
  из-за этого отклонён, и вино стало ненаходимым — см. пропуск F05.

## Проверка интерфейса стенда

`http://89.110.72.101/app/scan` открывается, экран скана в порядке. Карточка по слагу
(`/app/wine/<slug>`) отдаёт имя, винодельню, сорта, вкусовой профиль, «Похожие вина» и рабочую
ссылку «Открыть на «Своё Вино»» (`https://vino-svoe.ru/wines/<slug>`) — проверено на
`usadba-divnomorskoe-solnechnyy-veter-...` и `bukovinka`. У `bukovinka` карточка показывает
нормальную бутылку, т. е. плакат живёт только в `case-data/thumbs/`. `GET /v1/wines/<slug>` без
Bearer-токена отдаёт 401 — это ожидаемо, смотреть надо через интерфейс. `/v1/healthz`: `warm: true`,
`cv_index_version: case-20260921-d1-b384`.

## Предложения

1. Порог floor 0.80 отсекает 6 верных ответов из 15 пропусков, а перехлёст с ошибками в этой зоне —
   нулевой (единственный failed выше 0.80 имеет gap 0.004). Стоит померить floor 0.76–0.78 на
   размеченных 100 фото: по архиву это +6 верных top-1 без новых уверенных ошибок.
2. Контроль по цвету: сравнивать цвет жидкости в кадре с полем `color` каталога и снимать
   уверенность при расхождении красное↔розовое↔белое. Поймало бы ошибку №2 и половину близнецов.
3. 44 слага без эталона — это 44 вина, которые нельзя найти совсем; три из них уже всплыли в архиве
   за неделю. Если до сдачи есть время, эталоны стоит добрать хотя бы для крупных виноделен.
4. Дубли слагов: нужен канонический выбор внутри семьи (например, всегда слаг без числового
   суффикса), иначе на приватной проверке эти вина — монетка.

## Как воспроизвести

```
ls /Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/scan-archive/scans
python3 -c "import json;[print(json.loads(l)['id']) for l in open('.../scans/index.jsonl')]"
/Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye/packages/cv/.venv/bin/python \
  /Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/search_catalog.py --winery "Табия"
```
Уникальные фото считаются по md5 файла; склейка «фото + эталон» строится PIL-ом из
`case-data/thumbs/<slug>.webp`.

## Полная таблица

Столбцы: метка · файл · что прочитано с этикетки · что выдал сервис · вердикт · комментарий.

### confident (63 уникальных фото)

| # | файл | этикетка глазами | выдано | вердикт | комментарий |
|---|---|---|---|---|---|
| C01 | `20260921T094736_65779c.jpg` | Массандра Мускатель Массандра белый 2023 | massandra-muskatel-belyy-belye-sorta-vinograda-beloe-sladkoe-16 | верно |  |
| C02 | `20260922T102755_765144.jpg` | Denisov Красная стрелка Рубин | denisov_rubin_klaret_krasnaya_strelka | верно |  |
| C03 | `20260922T103258_325935.jpg` | Fanagoria Формула Q (КС21/Мерло22/Красностоп23/Саперави24) | fanagoriya-formula-q-saperavi-krasnoe-suhoe-135 | верно | в каталоге ещё «Декантер. Формула Q» с тем же составом — риск дубля |
| C04 | `20260922T103600_b01fba.jpg` | Скалистый берег Шёпот цветов | skalistyj-bereg-shepot-czvetov | верно |  |
| C05 | `20260922T120310_5608dd.jpg` | Жемчужная 9 Алиготе Цитрон 2024 | zhemchuzhnaya-9-aligote-czitron | верно |  |
| C06 | `20260922T120317_6da3e1.jpg` | Табия Победа, тираж 1945 | pobeda | верно | у слага нет превью в thumbs |
| C07 | `20260922T120323_0d7d79.jpg` | Реликта Сира Каберне Фран 2024 | relikta-relikta-sira-kaberne-fran-krasnoe-suhoe-13 | верно |  |
| C08 | `20260922T120330_972f6b.jpg` | Винодельня Узунов Шардоне Резерв 2024 | vinodelnya-uzunov-shardone-rezerv-beloe-suhoe-135 | верно | год фото 2024, эталон 2021 |
| C09 | `20260922T120356_9528d6.jpg` | Жемчужная 9 Цитрон Совиньон блан 2024 | czitronnyj-magaracha-sovinon-blan | верно |  |
| C10 | `20260922T120415_a05df5.jpg` | АРАТТИ Каберне Совиньон 2021 | aratti-kaberne-sovinon-2021-krasnoe-suhoe | верно | год совпал (в каталоге есть 2020) |
| C11 | `20260922T120428_8c4821.jpg` | ARATTI Белая Львица полусладкое | belaya-lvicza | верно |  |
| C12 | `20260922T120435_d6f948.jpg` | Аратти Каберне Совиньон 2020 | aratti-kaberne-sovinon-2020-krasnoe-suhoe | верно |  |
| C13 | `20260922T120441_8c12cb.jpg` | Денисов Совиньон Блан | denisov-winery-sovinon-blan-beloe-suhoe-115 | верно |  |
| C14 | `20260922T120448_132f97.jpg` | Аратти Каберне Совиньон 2020 | aratti-kaberne-sovinon-2020-krasnoe-suhoe | верно |  |
| C15 | `20260922T120501_a9a415.jpg` | Golubitskoe Estate Noble Selection Red Blend 2019 | golubitskoe-estate-noble-selection-red-blend-kaberne-sovinon-krasnoe-suhoe-136 | верно |  |
| C16 | `20260922T120507_1d0ac6.jpg` | Denisov Пет-Нат Рубин | denisov_pet_nat_rubin | верно |  |
| C17 | `20260922T120512_18ce46.jpg` | Денисов Закат | zakat-denisov-vajneri | верно |  |
| C18 | `20260922T120518_d41ede.jpg` | Денисов Закат | zakat-denisov-vajneri | верно |  |
| C19 | `20260922T120530_b7f0f9.jpg` | Golubitskoe Estate Chardonnay 2024 | golubitskoe-estate-chardonnay | верно |  |
| C20 | `20260922T120537_2dd568.jpg` | Табия Рубин Голодриги | rubin-golodrigi | верно |  |
| C21 | `20260922T120549_8ffa10.jpg` | Denisov ПаЗори белое брют 2022 | denisov_pazori_risling | верно |  |
| C22 | `20260927T002849_4db04a.jpg` | Desono Cabernet Sauvignon 2022 | derbent-vino-desono-kaberne-sovinon-krasnoe-suhoe-125 | верно | то же фото было unsure 22.09 |
| C23 | `20260922T120602_b7544a.jpg` | Жемчужная 9 Цитрон Совиньон блан 2024 | czitronnyj-magaracha-sovinon-blan | верно |  |
| C24 | `20260922T120621_d609e7.jpg` | Денисов Совиньон Блан | denisov-winery-sovinon-blan-beloe-suhoe-115 | верно |  |
| C25 | `20260922T120628_6a5fd1.jpg` | Жемчужная 9 Пино Нуар Мускат розовый, полусладкое 2024 | zhemchuzhnaya-9-pino-nuar-muskat-rozovyj-2 | верно | в каталоге 3 слага на это вино |
| C26 | `20260922T120635_7a155c.jpg` | Fanagoria Зелёное вино Рислинг-Цитронный Магарача | fanagoriya-zelyonoe-vino-risling-tsitronnyy-magaracha-beloe-polusuhoe-11 | верно |  |
| C27 | `20260922T120700_7ce6c4.jpg` | Жемчужная 9 Цитрон Шардоне полусладкое 2024 | zhemchuzhnaya-9-czitron-shardone-1 | верно | в каталоге 2 слага на это вино |
| C28 | `20260922T120714_1aa669.jpg` | АРАТТИ Каберне по-белому 2024 полусухое | aratti-kaberne-po-belomu-1 | верно | в каталоге 2 слага (Красное/Розовое); выбран розовый — верно |
| C29 | `20260922T120741_5cf2ce.jpg` | Жемчужная 9 Пино Нуар Мускат розовый, полусладкое 2024 | zhemchuzhnaya-9-pino-nuar-muskat-rozovyj-2 | верно | в каталоге 3 слага на это вино |
| C30 | `20260922T120747_1bf9b2.jpg` | Табия Рубин Голодриги | rubin-golodrigi | верно |  |
| C31 | `20260922T120753_742ec6.jpg` | Uva Vallis levitas Riesling 2023 Crimea | uva-vallis-risling | верно | в каталоге у слага color=Красное — дефект каталога |
| C32 | `20260922T120759_0d5b0f.jpg` | Denisov Курмыши (Барыня) | denisov_barynya_kurmyshi | верно |  |
| C33 | `20260922T120806_003d51.jpg` | Fanagoria Зелёное вино | fanagoriya-zelyonoe-vino-risling-tsitronnyy-magaracha-beloe-polusuhoe-11 | верно |  |
| C34 | `20260922T120812_977e49.jpg` | Табия Розе 2024 розовое сухое | roze-2 | верно | у слага нет превью |
| C35 | `20260922T120818_cc6841.jpg` | Денисов Совиньон Блан | denisov-winery-sovinon-blan-beloe-suhoe-115 | верно |  |
| C36 | `20260922T120825_dd15f2.jpg` | Усадьба Дивноморское, Терруар Вторая линия «Вечерница» (красное) | usadba-divnomorskoe-solnechnyy-veter-shardone-beloe-suhoe-125 | ОШИБКА | в каталоге у винодельни 17 вин, «Вечерницы» нет; выдан «Солнечный Ветер» (Шардоне белое) |
| C37 | `20260922T120838_62b040.jpg` | Жемчужная 9 Цитрон Шардоне 2024 | zhemchuzhnaya-9-czitron-shardone-1 | верно | в каталоге 2 слага |
| C38 | `20260922T120844_510193.jpg` | Golubitskoe Estate Merlot 2022 | golubitskoe-estate-merlo-krasnoe-suhoe-135 | верно |  |
| C39 | `20260922T120856_c1174b.jpg` | Табия Розе 2024 | roze-2 | верно |  |
| C40 | `20260922T120902_63035d.jpg` | Табия Розе 2024 сухое | roze-2 | верно | рядом на полке Розе полусухое |
| C41 | `20260922T120909_0350d8.jpg` | Жемчужная 9 Пино Нуар Мускат розовый, ПОЛУСУХОЕ 2024 | zhemchuzhnaya-9-pino-nuar-muskat-rozovyj-1 | верно | в каталоге 3 слага, сахар в названиях не различается |
| C42 | `20260922T120916_98cef9.jpg` | Денисов Совиньон Блан | denisov-winery-sovinon-blan-beloe-suhoe-115 | верно |  |
| C43 | `20260922T120922_68b804.jpg` | Усадьба Дивноморское «Вечерница» (второе фото) | usadba-divnomorskoe-solnechnyy-veter-shardone-beloe-suhoe-125 | ОШИБКА | то же, что C36 |
| C44 | `20260922T120936_2a1e4f.jpg` | АРАТТИ Каберне по-белому 2024 | aratti-kaberne-po-belomu-1 | верно |  |
| C45 | `20260922T120943_4f85bb.jpg` | Табия Буковинка | bukovinka | верно | в case-data/thumbs у слага лежит плакат винного форума (в slug_refs эталон уже заменён) |
| C46 | `20260922T121015_63de9a.jpg` | Абрау-Дюрсо Русское Игристое розовое полусухое | abrau-dyurso-russkoe-igristoe-polusuhoe-rozovoe-pino-nuar-12 | верно |  |
| C47 | `20260922T121022_d0f9de.jpg` | Табия Цитрон 2024 (конь) | czitronnyj-magaracha | верно |  |
| C48 | `20260922T121033_0522cd.jpg` | Denisov Рислинг | denisov-winery-risling-beloe-suhoe-115 | верно |  |
| C49 | `20260922T121046_b3e7cc.jpg` | Fanagoria Зелёное вино | fanagoriya-zelyonoe-vino-risling-tsitronnyy-magaracha-beloe-polusuhoe-11 | верно |  |
| C50 | `20260922T121053_d92191.jpg` | Золотая Балка Балаклава Muscat полусладкое | balaklava-muskat-beloe-polusladkoe | верно |  |
| C51 | `20260922T121059_756c10.jpg` | Fanagoria Зелёное вино | fanagoriya-zelyonoe-vino-risling-tsitronnyy-magaracha-beloe-polusuhoe-11 | верно |  |
| C52 | `20260922T121119_646093.jpg` | Усадьба Перовских Красное полусухое 2024 | perovskih_polusuhoe_krasnoe | верно |  |
| C53 | `20260922T121131_417dd1.jpg` | Усадьба Мезыбь Шишка Купаж №5 2023 | usadba-mezyb-shishka-kupazh-5-kaberne-sovinon-rozovoe-suhoe-133 | верно |  |
| C54 | `20260922T121138_cf5e9e.jpg` | Литавщук Поздний сбор 2023 сладкое красное | pozdnij-sbor-krasnoe | верно |  |
| C55 | `20260922T121144_3afcd8.jpg` | Golubitskoe Estate Winery Series RED BLEND (красное) | golubitskoe-estate-winery-series-roze-blend-kaberne-sovinon-rozovoe-suhoe-117 | ОШИБКА | выдан «Winery Series. Розе бленд» (розовое); в каталоге WS Red Blend нет, ближайшее — Red Blend (другая этикетка) |
| C56 | `20260922T121150_aba69d.jpg` | Табия «ЦИТРОННЫЙ МАГАРАЧА» 2024 (ферзь) | czitronnyj-magaracha | близнец | выдан слаг czitronnyj-magaracha = Табия «Цитрон» (конь) — соседняя бутылка той же серии |
| C57 | `20260922T121235_4cb9eb.jpg` | Табия Цитрон 2024 | czitronnyj-magaracha | верно |  |
| C58 | `20260922T121242_b7d8d2.jpg` | Массандра САПЕРАВИ ординарное сухое, урожай 2025 | caperavi-avtorskoe-vino | близнец | выдано «Cаперави. Авторское вино» — другая линейка; ординарного Саперави в каталоге нет |
| C59 | `20260922T164849_7074c5.jpg` | Золотая Балка Брют белое | bryut-beloe-zolotaya-balka | верно |  |
| C60 | `20260922T165115_3cdd44.jpg` | Château le Grand Vostock, Vostock sélection blanc | chteau-le-grand-vostock-vostock-blanc-sovinon-blan-beloe-suhoe-135 | верно |  |
| C61 | `20260926T185101_8ffcde.jpg` | Фанагория «Она сказала Да!» белое полусладкое | fanagoriya-ona-skazala-da-beloe-polusladkoe | верно | кадр — не фото, а каталожный рендер 203x630, 5 повторов |
| C62 | `20260927T011818_444683.jpg` | Табия Рубин Голодриги 2023 | rubin-golodrigi | верно | обрезанный кадр 1512x3225 |
| C63 | `20260927T012421_042690.jpg` | кадр — здание «Семейной винодельни Литавщуков», бутылки в кадре нет | lesnaya-proseka | ОШИБКА | выдано «Кубань-Вино. Лесная просека», score 0.853 |
### unsure (12 фото, снятых только в этой корзине; 13-е — `20260922T120555_7dceeb`, оно же C22, ответ верный)

| # | файл | этикетка глазами | выдано | вердикт | комментарий |
|---|---|---|---|---|---|
| U01 | `20260922T120648_7a2c39.jpg` | Golubitskoe Estate Pinot Noir 2023 (белая этикетка) | golubitskoe-estate-winery-series-pino-nuar-rozovoe-suhoe-115 | верный отказ | в каталоге только «Пино Нуар Резерв» с чёрной этикеткой |
| U02 | `20260922T120831_abbc9a.jpg` | Golubitskoe Estate Cabernet Sauvignon 2023 | golubitskoe-estate-kaberne-sovinon-krasnoe-suhoe-137 | ПРОПУСК | правильный слаг был top-1 (0.877), отклонён по margin 0.012 |
| U03 | `20260922T120955_06e33e.jpg` | Дивноморское «Вечерница» | usadba-divnomorskoe-yuzhnyy-les-merlo-krasnoe-suhoe-137 | верный отказ | вина нет в каталоге |
| U04 | `20260922T121106_58dee3.jpg` | Валерий Захарьин Авторское Каберне Совиньон 2024 | valeriy-zaharin-avtorskoe-vino-ot-valeriya-zaharina-bastardo-bastardo-magarachskiy-krasnoe-suhoe-12 | верный отказ | у винодельни 48 вин, авторского КС нет |
| U05 | `20260922T121210_e0ec03.jpg` | Дивноморское «Вечерница» | usadba-divnomorskoe-yuzhnyy-les-merlo-krasnoe-suhoe-137 | верный отказ | вина нет в каталоге |
| U06 | `20260923T145321_62848b.jpg` | Галицкий и Галицкий Каберне Совиньон, Красная горка 2020 (рендер) | kaberne-sovinon-1 | ПРОПУСК | правильный слаг top-1 (0.918), margin 0.008 против Appassimento |
| U07 | `20260923T145434_6b4f02.jpg` | Andryus Yutsis, микро-рендер 102×500 | andryus-yutsis-risling-beloe-suhoe-12 | не определить | текст этикетки нечитаем в оригинале |
| U08 | `20260923T145618_f56661.jpg` | Галицкий и Галицкий Каберне Совиньон (фото экрана) | kaberne-sovinon-appasimento | ПРОПУСК | top-1 был Appassimento (неверно), правильный — №2 |
| U09 | `20260923T194902_82ae7c.jpg` | WINEPARK Merlot/Cab Franc/Cab Sauv/Mourvedre 2021 (рендер) | winepark-kuchuk-isar-kaberne-sovinon-krasnoe-suhoe-135 | верный отказ | у WINEPARK в каталоге 4 вина, этого бленда нет |
| U10 | `20260926T182529_b76575.jpg` | тот же WINEPARK бленд (рендер на чёрном) | winepark-risling-beloe-suhoe-125 | верный отказ |  |
| U11 | `20260926T183138_61ed14.jpg` | Abrau-Durso Reserve Brut (рендер) | abrau-dyurso-abrau-durso-reserve-brut-shardone-beloe-bryut-115 | ПРОПУСК | правильный слаг top-1 (0.946), margin 0.006 против Brut Rose Reserve |
| U12 | `20260927T012340_dcc246.jpg` | пейзаж: озеро, камни, горы | agrolayn-lame-du-vin-saperavi-krasnoe-suhoe-13 | не вино | отказ верный, но score 0.837 |
### failed (44 фото; 45-е — `20260922T120343_3cbafa` / `20260927T002633_90df2e`, тот же кадр, что C01)

| # | файл | этикетка глазами | выдано | вердикт | комментарий |
|---|---|---|---|---|---|
| F01 | `20260921T094730_35baff.jpg` | Табия Пино Нуар полусухое 2025 | usadba-mezyb-shishka-pino-nuar-rozovoe-suhoe-115 | верный отказ |  |
| F02 | `20260921T094741_87a211.jpg` | Aristov DONUM XXIV брют 2023 | kuban-vino-aristov-anima-michela-2023-sandzhoveze-krasnoe-suhoe-115 | верный отказ |  |
| F03 | `20260922T113229_791c6a.jpg` | Фанагория ΛΗΚΥΘΟΣ «Лекиф» | fanagoriya-dekanter-merlo-2018-krasnoe-suhoe-14 | верный отказ | эталонный NONE из протокола разметки |
| F04 | `20260922T113235_18cfca.jpg` | Château Pinot «Беленькое» 2025 | chteau-le-grand-vostock-pinot-gris-reserve-pino-gri-beloe-suhoe-135 | верный отказ |  |
| F05 | `20260922T113254_b9368f.jpg` | Фанагория Velvet Season MUSCAT сладкое | fanagoriya-velvet-season-muskat-ottonel-beloe-sladkoe-13 | ПРОПУСК | слаг есть, но у него НЕТ пригодного эталона (manual_override_rejected); в thumbs лежит бутылка Saperavi |
| F06 | `20260922T120257_c8b646.jpg` | Château Pinot «Беленькое» 2025 | chteau-le-grand-vostock-pinot-gris-reserve-pino-gri-beloe-suhoe-135 | верный отказ |  |
| F07 | `20260922T120336_1b5bfd.jpg` | AYA Purity In Balance 2025 | aya-organic-wine-vineyards-purity-in-chenin-blanc-shenen-blan-beloe-suhoe-11 | верный отказ | в каталоге Purity in Chenin Blanc/Merlot/Syrah/Trinity |
| F08 | `20260922T120349_dcf847.jpg` | Inkerman Каберне сухое красное | inkerman-shato-ruzh | верный отказ |  |
| F09 | `20260922T120403_6cf1f4.jpg` | Табия «Розовое золото» 2024 полусухое | rozovoe-zoloto | ПРОПУСК | правильный слаг top-1, score 0.797 при пороге 0.80 |
| F10 | `20260922T120409_236508.jpg` | Inkerman «Буссо» полусладкое розовое | inkermanskiy-zmv-inkerman-rozovoe-polusladkoe-rkatsiteli-12 | верный отказ |  |
| F11 | `20260922T120422_045363.jpg` | Семейная винодельня Литавщуков, Мерло сухое | soyuz-vino-kubanskoe-traditsionnoe-krasnoe-suhoe-07-krasnye-sorta-vinograda-11 | ПРОПУСК | merlo-litavshhuk есть в каталоге, но его нет даже в top-5 |
| F12 | `20260922T120455_e2d3a6.jpg` | MONT DE FLEUR розовое полусухое | rozovoe-bryut | верный отказ |  |
| F13 | `20260922T120524_582161.jpg` | Фанагория ALVEUS Ультра Кюве ОРАНЖ брют | fanagoriya-alveus-ultra-cuvee-brut-shardone-beloe-bryut-12 | верный отказ | в каталоге Alveus только брют/экстра-брют белое и розовое |
| F14 | `20260922T120543_025f10.jpg` | Фанагория ALVEUS Оранж брют | fanagoriya-alveus-ultra-cuvee-brut-shardone-beloe-bryut-12 | верный отказ |  |
| F15 | `20260922T120609_5f1319.jpg` | Château Pinot «Беленькое» 2025 | chteau-le-grand-vostock-pinot-noir-reserve-pino-nuar-krasnoe-suhoe-135 | верный отказ |  |
| F16 | `20260922T120615_ff9ab7.jpg` | MOGZAURI Алазанская долина (Грузия) | vinodelnya-vedernikov-vedernikov-dolina-dona-krasnoe-suhoe-kaberne-sovinon-125 | верный отказ | иностранное вино |
| F17 | `20260922T120641_9f7d70.jpg` | AYA Khrustaleva 76 Extra Brut Pinot Gris 2025 | fanagoriya-fanagoria-extra-brut-rose-2019-pino-nuar-igristoe-bryut-rozovoe-ekstra-bryut-12 | верный отказ |  |
| F18 | `20260922T120654_e86124.jpg` | Фанагория ALVEUS Оранж брют | fanagoriya-alveus-ultra-cuvee-ekstra-bryut-rozovoe-merlo-12 | верный отказ |  |
| F19 | `20260922T120707_018dfe.jpg` | Табия Пино Нуар полусухое 2025 | fanagoriya-100-ottenkov-krasnogo-pino-nuar-krasnoe-suhoe-135 | верный отказ |  |
| F20 | `20260922T120721_347573.jpg` | Литавщуков Совиньон Блан полусладкое | soyuz-vino-kubanskoe-traditsionnoe-beloe-polusladkoe-belye-sorta-vinograda-11 | верный отказ |  |
| F21 | `20260922T120727_7e64cb.jpg` | Фанагория ALVEUS Оранж брют | fanagoriya-alveus-ultra-cuvee-brut-shardone-beloe-bryut-12 | верный отказ |  |
| F22 | `20260922T120734_20d0c3.jpg` | Inkerman Riesling 2025 Winemaker’s Selection | winemaker-selection | ПРОПУСК | слаг winemaker-selection (Рислинг, Белое) — top-1 0.761, но у слага НЕТ эталона |
| F23 | `20260922T120850_665450.jpg` | Aristov DONUM XXIV брют 2023 | kuban-vino-aristov-anima-michela-2023-sandzhoveze-krasnoe-suhoe-115 | верный отказ |  |
| F24 | `20260922T120929_a8fb38.jpg` | Левъ Голицынъ Коронационное брют | abrau-dyurso-brut-dor-blanc-de-blancs-shardone-beloe-bryut-12 | верный отказ |  |
| F25 | `20260922T120949_93594d.jpg` | Фанагория Пино Гриджио Сюр Ли | fanagoriya-fanagoria-extra-brut-rose-2019-pino-nuar-igristoe-bryut-rozovoe-ekstra-bryut-12 | верный отказ | в каталоге Sur Lie: Chardonnay/Clairet/Vi Vi |
| F26 | `20260922T121008_106d6f.jpg` | Литавщуков Совиньон Блан полусухое | lorio-sovinon-blan-2025 | верный отказ |  |
| F27 | `20260922T121028_9384e3.jpg` | Фанагория RS White Premium | fanagoriya-fanagoria-extra-brut-rose-2019-pino-nuar-igristoe-bryut-rozovoe-ekstra-bryut-12 | верный отказ |  |
| F28 | `20260922T121040_1d71cb.jpg` | Новый Свѣтъ выдержанное 2023 полусладкое розовое | novyy-svet-dom-shampanskih-vin-rossiyskoe-shampanskoe-vyderzhannoe-polusladkoe-rozovoe-novyy-svet-shardone-125 | ПРОПУСК | правильный слаг top-1, score 0.779 |
| F29 | `20260922T121113_e087d0.jpg` | Литавщуков Каберне Совиньон полусухое | semeynaya-vinodelnya-mihaila-kolesnikova-ispanets-kaberne-sovinon-krasnoe-suhoe-148 | верный отказ |  |
| F30 | `20260922T121125_30e447.jpg` | BelColle Barolo (Италия) | vibes-glera-col-fondo-2022 | верный отказ | иностранное вино |
| F31 | `20260922T121156_bde664.jpg` | Табия «Олег» 2025 белое сухое | oleg | ПРОПУСК | слаг oleg — top-1 0.727 |
| F32 | `20260922T121203_1052f7.jpg` | Ведерниковъ Долина Дона, Рислинг-Ркацители-Сибирьковый, белое сухое | vinodelnya-vedernikov-vedernikov-dolina-dona-beloe-suhoe-aligote-12 | ПРОПУСК | правильный слаг top-1, score 0.785 |
| F33 | `20260922T121216_b2d625.jpg` | Табия «Олег» 2025 | oleg | ПРОПУСК | слаг oleg — top-1 0.783 |
| F34 | `20260922T121222_731577.jpg` | Литавщуков Совиньон Блан полусухое | lorio-sovinon-blan-2025 | верный отказ |  |
| F35 | `20260922T121229_d78a92.jpg` | Denisov «Красная стрелка» Рубин | denisov_rubin_klaret_krasnaya_strelka | ПРОПУСК | то же вино уверенно узнано на другом фото (C02); здесь 0.776 |
| F36 | `20260922T121248_96d355.jpg` | Фанагория ALVEUS Оранж брют | fanagoriya-alveus-ultra-cuvee-brut-shardone-beloe-bryut-12 | верный отказ |  |
| F37 | `20260922T121254_bc4cf5.jpg` | Литавщуков Совиньон Блан полусухое | lorio-sovinon-blan-2025 | верный отказ |  |
| F38 | `20260922T154458_1fb059.jpg` | кадр — стеллаж магазина целиком | massandra-portveyn-belyy-alushta-belye-sorta-vinograda-beloe-sladkoe-17 | не вино | отказ верный |
| F39 | `20260922T154522_5fcb22.jpg` | Di Caspico Fiori di Mare Verde (в центре полки) | di-kaspiko-di-caspico-fiori-di-mare-verde | ПРОПУСК | правильный слаг top-1, score 0.688 |
| F40 | `20260922T164740_7b5451.jpg` | Фанагория Cru Lermont Riesling 2024 | cru-lermont-risling | ПРОПУСК | слаг cru-lermont-risling — top-1 0.698, у слага НЕТ эталона |
| F41 | `20260922T165014_095bab.jpg` | Фанагория РОЗЕ «Румянец» | fanagoriya-100-ottenkov-krasnogo-pino-nuar-krasnoe-suhoe-135 | верный отказ |  |
| F42 | `20260925T153243_8f68ee.jpg` | кадр — стеллаж магазина | esse-shenen-blan-beloe-suhoe-13 | не вино | отказ верный |
| F43 | `20260925T155158_a38e40.jpg` | кадр — стеллаж магазина | derbent-vino-endemy-sovinon-beloe-bryut-105-125 | не вино | отказ верный |
| F44 | `20260926T184957_a7dfa2.jpg` | Фанагория «R» Шардоне сухое белое (рендер) | fanagoriya-fanagoriya-blanc-de-blancs-beloe-iz-belogo-shardone-igristoe-bryut-beloe-11-13 | верный отказ |  |
