# Демо-пак: Абрау-Дюрсо

Сгенерировано: 2026-08-27T08:57:15+00:00 · svoy-somelye-demo-pack v1.0.0 · slug `abrau-dyurso`

## Винодельня

- Название: Абрау-Дюрсо
- Регион: Кубань
- Основана: 1870
- Вин в каталоге: 51 (пригодных к отбору: 51, исключено по неполноте: 0)
- Первоисточник: https://vino-svoe.ru/wineries/abrau-dyurso

## Топ-8 бутылок (по рейтингу и полноте карточки)

| # | Вино | Цвет / сахар | Рейтинг | Сорта | Гастропары |
|---|------|--------------|---------|-------|------------|
| 1 | [Abrau Estates Амурский Потапенко](https://vino-svoe.ru/wines/abrau-dyurso-abrau-estates-amurskiy-potapenko-krasnoe-suhoe-105) | красное / сухое | 5.0 | Амурский Потапенко | Мясное ассорти, Сыры |
| 2 | [Abrau Estates красное](https://vino-svoe.ru/wines/abrau-dyurso-abrau-estates-krasnoe-kaberne-sovinon-suhoe-13) | красное / сухое | 5.0 | Каберне Совиньон, Мерло | Мясное ассорти, Сыры |
| 3 | [Abrau-Durso Brut Rose Reserve](https://vino-svoe.ru/wines/abrau-dyurso-abrau-durso-brut-rose-reserve-pino-nuar-beloe-bryut-12) | белое / брют | 5.0 | Каберне Совиньон, Каберне Фран, Мерло, Пино Нуар, Пино фран | Легкие закуски, Морепродукты, Сыры |
| 4 | [Abrau-Durso Reserve Brut](https://vino-svoe.ru/wines/abrau-dyurso-abrau-durso-reserve-brut-shardone-beloe-bryut-115) | белое / брют | 5.0 | Пино Блан, Рислинг, Шардоне | Легкие закуски, Морепродукты |
| 5 | [Brut d'Or Blanc de Noirs](https://vino-svoe.ru/wines/abrau-dyurso-brut-dor-blanc-de-noirs-pino-nuar-beloe-bryut-125) | белое / брют | 5.0 | Пино Нуар | Мясное ассорти, Сыры, Фрукты |
| 6 | [Brut d'Or Rose](https://vino-svoe.ru/wines/abrau-dyurso-brut-dor-rose-pino-nuar-rozovoe-bryut-12) | розовое / брют | 5.0 | Пино Нуар | Сыры |
| 7 | [Brut d'Or. Blanc de Blancs](https://vino-svoe.ru/wines/abrau-dyurso-brut-dor-blanc-de-blancs-shardone-beloe-bryut-125) | белое / брют | 5.0 | Шардоне | Легкие закуски, Морепродукты, Салаты |
| 8 | [Brut d'Or. Riesling](https://vino-svoe.ru/wines/abrau-dyurso-brut-dor-riesling-risling-beloe-bryut-12) | белое / брют | 5.0 | Рислинг | Морепродукты, Сыры |

Методология: composite = 0.65×(рейтинг/5, 0 если рейтинга нет) + 0.35×(доля заполненных необязательных полей из ['vintage', 'public_rating', 'color_in_glass', 'abv_percent', 'similar_wine_slugs', 'derived.reference_style_matches']). Карточки без обязательных полей ('name', 'color', 'sugar_category', 'grapes', 'food_pairings', 'description', 'image_url') исключаются целиком (см. excluded_incomplete), не понижаются в ранге. Равенство composite решается по рейтингу, затем по имени (детерминированность между прогонами).

## Сценарий показа (три сцены)

### Сцена 1 — Скан

- Бутылка: **Abrau Estates Амурский Потапенко** (Абрау-Дюрсо), `abrau-dyurso-abrau-estates-amurskiy-potapenko-krasnoe-suhoe-105`
- Живая бутылка «Abrau Estates Амурский Потапенко» (Абрау-Дюрсо) из демо-набора, куплена под этот пак — см. qa/demo-script.md.
- Текст на случай сканера текстом / ручного ввода: «Abrau Estates Амурский Потапенко, Абрау-Дюрсо»
- Ожидаемая карточка: красное, сухое, рейтинг 5.0, гастропары: Мясное ассорти, Сыры
- Первоисточник карточки: https://vino-svoe.ru/wines/abrau-dyurso-abrau-estates-amurskiy-potapenko-krasnoe-suhoe-105
- Критерий успеха: Скан -> карточка ≤3 секунды (mvp-plan.html, раздел 0); карточка показывает рейтинг, гастропары и рабочую ссылку на первоисточник.

### Сцена 2 — Вопрос сомелье

- Вопрос (по реальной гастропаре пака, встречается у 6 из 8 бутылок): «Что взять к сырам?»
- Гастропара: Сыры
- Ожидаемые вина в ответе (>=1 из перечисленных, ответ обязан нести >=2 цитаты):
  - `abrau-dyurso-abrau-estates-amurskiy-potapenko-krasnoe-suhoe-105` — Abrau Estates Амурский Потапенко: «В аромате цветочные ноты пионов и фиалок, тона черных ягод и фруктов – шелковицы, черной смородины и ежевики с нюансами пряностей. Во вкусе ноты синей сливы,…»
  - `abrau-dyurso-abrau-estates-krasnoe-kaberne-sovinon-suhoe-13` — Abrau Estates красное: «В аромате фрукты, табак, чернослив, шоколад и мускатный орех. Вкус кислотный, свежий, с мягкими танинами, легкими животными и табачными нюансами в послевкусии.»
  - `abrau-dyurso-abrau-durso-brut-rose-reserve-pino-nuar-beloe-bryut-12` — Abrau-Durso Brut Rose Reserve: «В аромате красные лесные ягоды — малина, вишня и земляника, оттенки пенки малинового варенья и клубники со сливками. Вкус округлый и мягкий, свежая кислотнос…»
  - `abrau-dyurso-brut-dor-blanc-de-noirs-pino-nuar-beloe-bryut-125` — Brut d'Or Blanc de Noirs: «В аромате тона смородины, земляники и вишни, ноты пастилы и зефира. Вкус яркий, полный и округлый с кремовой текстурой.»
  - `abrau-dyurso-brut-dor-rose-pino-nuar-rozovoe-bryut-12` — Brut d'Or Rose: «В аромате красная смородина, малина и клюква, цветочные ноты и тонкие ароматы специй. Вкус яркий, с насыщенной текстурой, с выраженной кислотностью. Ноты зеф…»
  - `abrau-dyurso-brut-dor-riesling-risling-beloe-bryut-12` — Brut d'Or. Riesling: «В аромате цитрусовые, белые цветы, груши и зеленые яблоки. минеральность. Во вкусе баланс кислотности, ноты яблока, дюшеса и желтой сливы. Послевкусие яркое…»
- Критерий успеха: Ответ несёт >=2 цитаты из базы (mvp-plan.html, раздел 0) и явно называет хотя бы одно из перечисленных вин пака — без выдумки на пустой выдаче.
- Точный текст ответа формирует LLM/RAG во время показа (не в этом генераторе) — здесь зафиксированы факты, обязанные попасть в ответ: сами вина и их реальные гастропары из каталога, а не дословная цитата.

### Сцена 3 — Аналог импортного

- Реплика пользователя: «Люблю шампанское брют без года»
- Эталонный стиль: Шампанское брют без года (Франция), slug `champagne-brut-nv`
- Ожидаемое вино пака: **Abrau-Durso Brut Rose Reserve** (`abrau-dyurso-abrau-durso-brut-rose-reserve-pino-nuar-beloe-bryut-12`)
- Критерий успеха: Ответ предлагает российское вино в стиле «Шампанское брют без года» (Франция) — как минимум Abrau-Durso Brut Rose Reserve (abrau-dyurso-abrau-durso-brut-rose-reserve-pino-nuar-beloe-bryut-12) из пака.

## Автопроверка

- Обязательные поля непусты у всех 8 бутылок: OK
- source_url отвечает 200 на вежливый HEAD: проверено 9 уникальных ссылок, предупреждений: 0
- Сцена 2 опирается на реальную гастропару пака: OK
- Сцена 3 — аналог подтверждён в derived.reference_style_matches: OK

**Вердикт: пак готов к показу.**
