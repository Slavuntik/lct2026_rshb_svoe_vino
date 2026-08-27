"""Фикстуры мок-RAG: 6 ВЫМЫШЛЕННЫХ вин + несколько текстовых чанков "знаний" +
6 эталонных стилей. Ничего из настоящего каталога vines сюда не попадает —
агент B работает по contracts/rag-interface.md через мок, не дожидаясь
агента A и не трогая read-only данные vines (ORCHESTRATION.md, правило 5).

Форма каждого вина намеренно похожа на catalog/wines/<slug>.json (source +
derived), чтобы GET /wines/{wine_id} отдавал реалистичную форму ответа уже
сейчас и не менялся при подключении настоящего packages/rag.

URL первоисточника — example.com (RFC 2606, гарантированно не резолвится в
реальный контент): это вымышленные вина, а не пропущенные через RAG данные
реального винного каталога.
"""
from __future__ import annotations

WINES: list[dict] = [
    {
        "slug": "shato-vymysel-cabernet",
        "name": "Шато Вымысел Каберне Совиньон",
        "winery": "shato-vymysel",
        "winery_name": "Шато Вымысел",
        "region": "kuban",
        "region_name": "Кубань",
        "grapes": ["Каберне Совиньон"],
        "color": "красное",
        "sugar_category": "сухое",
        "color_in_glass": "гранатовый",
        "vintage": 2022,
        "abv_percent": 13.5,
        "serving_temp_c": [16, 18],
        "food_pairings": ["Мясо и стейки", "Твёрдые сыры"],
        "description": (
            "Вкус: плотный, танинный, с нотами чёрной смородины и умеренной дубовой выдержкой. "
            "Хорошо раскрывается через 20-30 минут после откупоривания."
        ),
        "image_url": "https://example.com/mock-catalog/img/shato-vymysel-cabernet.webp",
        "similar_wine_slugs": ["tihaya-gavan-pinot-noir"],
        "source_url": "https://example.com/mock-catalog/wines/shato-vymysel-cabernet",
        "derived": {
            "stillness": "тихое",
            "style_tags": ["танинное", "полнотелое"],
            "grape_slugs": ["cabernet-sauvignon"],
            "reference_style_matches": ["valpolicella"],
            "sensory": {
                "sweetness": 0.05, "acidity": 0.55, "tannin": 0.65, "body": 0.7,
                "oak": 0.3, "aromatic_intensity": 0.5, "bubbles": 0.0,
                "confidence": 0.8, "method": "fixture",
            },
        },
    },
    {
        "slug": "belye-peski-sauvignon-blanc",
        "name": "Белые Пески Совиньон Блан",
        "winery": "belye-peski",
        "winery_name": "Белые Пески",
        "region": "taman",
        "region_name": "Тамань",
        "grapes": ["Совиньон Блан"],
        "color": "белое",
        "sugar_category": "сухое",
        "color_in_glass": "соломенный",
        "vintage": 2024,
        "abv_percent": 12.0,
        "serving_temp_c": [8, 10],
        "food_pairings": ["Рыба и морепродукты", "Лёгкие салаты"],
        "description": (
            "Вкус: свежий, с выраженной цитрусовой кислотностью и травянистыми тонами "
            "крыжовника."
        ),
        "image_url": "https://example.com/mock-catalog/img/belye-peski-sauvignon-blanc.webp",
        "similar_wine_slugs": [],
        "source_url": "https://example.com/mock-catalog/wines/belye-peski-sauvignon-blanc",
        "derived": {
            "stillness": "тихое",
            "style_tags": ["свежее", "цитрусовое"],
            "grape_slugs": ["sauvignon-blanc"],
            "reference_style_matches": ["chablis"],
            "sensory": {
                "sweetness": 0.05, "acidity": 0.8, "tannin": 0.0, "body": 0.35,
                "oak": 0.05, "aromatic_intensity": 0.7, "bubbles": 0.0,
                "confidence": 0.8, "method": "fixture",
            },
        },
    },
    {
        "slug": "rozovyy-mirazh",
        "name": "Розовый Мираж",
        "winery": "mirazh-estate",
        "winery_name": "Мираж Эстейт",
        "region": "crimea",
        "region_name": "Крым",
        "grapes": ["Каберне Совиньон", "Мерло"],
        "color": "розовое",
        "sugar_category": "полусухое",
        "color_in_glass": "лососевый",
        "vintage": 2024,
        "abv_percent": 12.5,
        "serving_temp_c": [10, 12],
        "food_pairings": ["Лёгкие закуски", "Азиатская кухня"],
        "description": "Вкус: деликатный, с нотами клубники и арбуза, лёгкая сладость на фоне.",
        "image_url": "https://example.com/mock-catalog/img/rozovyy-mirazh.webp",
        "similar_wine_slugs": [],
        "source_url": "https://example.com/mock-catalog/wines/rozovyy-mirazh",
        "derived": {
            "stillness": "тихое",
            "style_tags": ["фруктовое", "лёгкое"],
            "grape_slugs": ["cabernet-sauvignon", "merlot"],
            "reference_style_matches": ["rose-d-anjou"],
            "sensory": {
                "sweetness": 0.3, "acidity": 0.6, "tannin": 0.1, "body": 0.4,
                "oak": 0.0, "aromatic_intensity": 0.55, "bubbles": 0.0,
                "confidence": 0.75, "method": "fixture",
            },
        },
    },
    {
        "slug": "igristoe-nebo-brut",
        "name": "Игристое Небо Брют",
        "winery": "nebo-vinery",
        "winery_name": "Винодельня Небо",
        "region": "don",
        "region_name": "Дон",
        "grapes": ["Шардоне", "Пино Нуар"],
        "color": "белое",
        "sugar_category": "брют",
        "color_in_glass": "светло-золотистый",
        "vintage": None,
        "abv_percent": 11.5,
        "serving_temp_c": [6, 8],
        "food_pairings": ["Закуски", "Морепродукты", "Фуршет"],
        "description": "Вкус: свежий, с мелким игристым перляжем и тонами зелёного яблока.",
        "image_url": "https://example.com/mock-catalog/img/igristoe-nebo-brut.webp",
        "similar_wine_slugs": [],
        "source_url": "https://example.com/mock-catalog/wines/igristoe-nebo-brut",
        "derived": {
            "stillness": "игристое",
            "style_tags": ["свежее", "фруктовое"],
            "grape_slugs": ["chardonnay", "pinot-noir"],
            "reference_style_matches": ["prosecco"],
            "sensory": {
                "sweetness": 0.05, "acidity": 0.7, "tannin": 0.0, "body": 0.4,
                "oak": 0.0, "aromatic_intensity": 0.6, "bubbles": 0.9,
                "confidence": 0.8, "method": "fixture",
            },
        },
    },
    {
        "slug": "sladkiy-zakat-muskat",
        "name": "Сладкий Закат Мускат",
        "winery": "zakat-vinery",
        "winery_name": "Винодельня Закат",
        "region": "taman",
        "region_name": "Тамань",
        "grapes": ["Мускат"],
        "color": "белое",
        "sugar_category": "сладкое",
        "color_in_glass": "янтарный",
        "vintage": 2023,
        "abv_percent": 11.0,
        "serving_temp_c": [8, 10],
        "food_pairings": ["Десерты", "Фрукты"],
        "description": "Вкус: насыщенный, медовый, с выраженным мускатным ароматом и нотами кураги.",
        "image_url": "https://example.com/mock-catalog/img/sladkiy-zakat-muskat.webp",
        "similar_wine_slugs": [],
        "source_url": "https://example.com/mock-catalog/wines/sladkiy-zakat-muskat",
        "derived": {
            "stillness": "тихое",
            "style_tags": ["сладкое", "ароматное"],
            "grape_slugs": ["muscat"],
            "reference_style_matches": ["sauternes"],
            "sensory": {
                "sweetness": 0.85, "acidity": 0.4, "tannin": 0.0, "body": 0.5,
                "oak": 0.1, "aromatic_intensity": 0.8, "bubbles": 0.0,
                "confidence": 0.8, "method": "fixture",
            },
        },
    },
    {
        "slug": "tihaya-gavan-pinot-noir",
        "name": "Тихая Гавань Пино Нуар",
        "winery": "gavan-estate",
        "winery_name": "Гавань Эстейт",
        "region": "kuban",
        "region_name": "Кубань",
        "grapes": ["Пино Нуар"],
        "color": "красное",
        "sugar_category": "сухое",
        "color_in_glass": "рубиновый",
        "vintage": 2022,
        "abv_percent": 13.0,
        "serving_temp_c": [14, 16],
        "food_pairings": ["Птица", "Лёгкое мясо", "Сыры"],
        "description": "Вкус: ягодный, с тонами вишни и лесных ягод, мягкие деликатные танины.",
        "image_url": "https://example.com/mock-catalog/img/tihaya-gavan-pinot-noir.webp",
        "similar_wine_slugs": ["shato-vymysel-cabernet"],
        "source_url": "https://example.com/mock-catalog/wines/tihaya-gavan-pinot-noir",
        "derived": {
            "stillness": "тихое",
            "style_tags": ["ягодное", "лёгкое"],
            "grape_slugs": ["pinot-noir"],
            "reference_style_matches": ["cru-beaujolais"],
            "sensory": {
                "sweetness": 0.05, "acidity": 0.7, "tannin": 0.4, "body": 0.48,
                "oak": 0.1, "aromatic_intensity": 0.5, "bubbles": 0.0,
                "confidence": 0.75, "method": "fixture",
            },
        },
    },
]

WINES_BY_SLUG: dict[str, dict] = {w["slug"]: w for w in WINES}


# "Знания" — чанки для /chat кроме самих вин (советы по подаче/сочетаниям).
# article_id/title/heading/rubric — форма Candidate.meta для kind=chunk,
# зафиксированная contracts/rag-interface.md v0.3 (см. packages/rag/rag/meta.py
# у агента A — то же самое public_meta, независимо реализованное по одному контракту).
KNOWLEDGE_CHUNKS: list[dict] = [
    {
        "id": "article:steak-pairing#1",
        "text": (
            "К стейку из мраморной говядины хорошо подходят плотные танинные красные вина — "
            "танины уравновешивают жирность мяса, а кислотность освежает нёбо."
        ),
        "url": "https://example.com/mock-catalog/articles/steak-pairing",
        "article_id": "steak-pairing",
        "title": "Что подать к стейку",
        "heading": "Красное мясо и танины",
        "rubric": "Сочетания",
    },
    {
        "id": "article:sparkling-serving#1",
        "text": (
            "Игристые вина брют подают охлаждёнными до 6-8°C в высоком узком бокале — "
            "так дольше сохраняется перляж."
        ),
        "url": "https://example.com/mock-catalog/articles/sparkling-serving",
        "article_id": "sparkling-serving",
        "title": "Как подавать игристое",
        "heading": "Температура и бокалы",
        "rubric": "Подача",
    },
    {
        "id": "article:dessert-wine-pairing#1",
        "text": (
            "Сладкие мускатные вина традиционно подают к фруктовым десертам и мягким сырам — "
            "избыточная сладость десерта иначе перебивает вино."
        ),
        "url": "https://example.com/mock-catalog/articles/dessert-wine-pairing",
        "article_id": "dessert-wine-pairing",
        "title": "Сладкие вина и десерты",
        "heading": "Мускат и фрукты",
        "rubric": "Сочетания",
    },
]


# Эталонные стили для /analogs (resolve_style + analog_for_style). slug'и и
# структура похожи на ref/reference_styles.yaml реального каталога, но набор
# полностью замкнут на 6 вымышленных вин выше.
REFERENCE_STYLES: list[dict] = [
    {
        "slug": "prosecco",
        "name": "Просекко",
        "country": "Италия",
        "synonyms": ["prosecco", "просекко", "итальянское игристое"],
    },
    {
        "slug": "chablis",
        "name": "Шабли",
        "country": "Франция",
        "synonyms": ["chablis", "шабли", "белое бургундское"],
    },
    {
        "slug": "rose-d-anjou",
        "name": "Розе д'Анжу",
        "country": "Франция",
        "synonyms": ["rose d'anjou", "розе д'анжу", "розовое луарское"],
    },
    {
        "slug": "sauternes",
        "name": "Сотерн",
        "country": "Франция",
        "synonyms": ["sauternes", "сотерн", "сладкое бордоское"],
    },
    {
        "slug": "cru-beaujolais",
        "name": "Крю Божоле",
        "country": "Франция",
        "synonyms": ["beaujolais", "божоле", "гаме"],
    },
    {
        "slug": "valpolicella",
        "name": "Вальполичелла",
        "country": "Италия",
        "synonyms": ["valpolicella", "вальполичелла", "итальянское красное"],
    },
]

REFERENCE_STYLES_BY_SLUG: dict[str, dict] = {s["slug"]: s for s in REFERENCE_STYLES}

INDEX_VERSION = "mock-fixtures-0.2"
