"""«Цифровой сомелье»: подбор вина из каталога по ответам на 3–4 вопроса-кнопки.

По обзору (docs/RESEARCH.md, раздел 5): LLM хуже всего справляются с сочетаниями вина и еды
(SommBench) и выдумывают позиции вне каталога, поэтому подбор здесь детерминированный —
правила сочетаний по цвету, сладости, игристости, сорту и признакам выдержки из описания.
LLM, если подключена, может только переформулировать объяснение для уже выбранных вин.

Тон объяснений — информационный, без призывов и оценок (38-ФЗ).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from winescan.search.text_match import tokens

# Правила: цвет -> вес, сладость -> вес, сорта (подстроки в транслите) -> вес; игристое -> вес.
DISHES: dict[str, dict] = {
    "meat": {
        "title": "мясо и стейки",
        "category": {"Красное": 2.0},
        "sweetness": {"dry": 1.0, "semi_dry": 0.3},
        "grapes": {"kaberne sovinon": 1.0, "saperavi": 1.0, "krasnostop": 1.0, "merlo": 0.6, "sira": 0.8,
                   "shiraz": 0.8, "malbek": 0.8, "tsimlyansk": 0.6},  # fmt: skip
        "sparkling": -1.5,
    },
    "poultry": {
        "title": "птица",
        "category": {"Белое": 1.0, "Розовое": 1.0, "Красное": 0.6},
        "sweetness": {"dry": 1.0, "semi_dry": 0.5},
        "grapes": {"pino nuar": 1.0, "shardone": 0.8, "rislin": 0.5, "rkaciteli": 0.4},
        "sparkling": -0.5,
    },
    "fish": {
        "title": "рыба и морепродукты",
        "category": {"Белое": 2.0, "Розовое": 0.8},
        # на реальном каталоге при брюте 1,0 и бонусе игристому 0,5 все три места занимали брюты
        "sweetness": {"dry": 1.0, "brut": 0.6, "extra_brut": 0.6, "semi_dry": 0.2},
        "grapes": {"rislin": 1.0, "aligote": 1.0, "sovinon blan": 1.0, "shardone": 0.6, "kokur": 0.8,
                   "rkaciteli": 0.6, "sibirkov": 0.6},  # fmt: skip
        "sparkling": 0.0,
    },
    "cheese": {
        "title": "сыры",
        "category": {"Красное": 1.0, "Белое": 1.0},
        "sweetness": {"dry": 0.8, "semi_sweet": 0.5, "sweet": 0.5},
        "grapes": {"kaberne": 0.6, "shardone": 0.6, "muskat": 0.4},
        "sparkling": 0.0,
    },
    "dessert": {
        "title": "десерты",
        "category": {"Белое": 0.8, "Розовое": 0.8, "Красное": 0.5},
        "sweetness": {"sweet": 2.0, "semi_sweet": 1.5, "semi_dry": 0.3},
        "grapes": {"muskat": 1.2, "kokur": 0.5},
        "sparkling": 0.3,
    },
    "vegetables": {
        "title": "овощи и салаты",
        "category": {"Белое": 1.5, "Розовое": 1.5, "Оранжевое": 1.0},
        "sweetness": {"dry": 1.0, "semi_dry": 0.6},
        "grapes": {"sovinon blan": 1.0, "rislin": 0.6, "aligote": 0.6, "roze": 0.4},
        "sparkling": 0.0,
    },
    "spicy": {
        "title": "острые и азиатские блюда",
        "category": {"Белое": 1.5, "Розовое": 1.0},
        "sweetness": {"semi_dry": 1.5, "semi_sweet": 0.8, "dry": 0.3},
        "grapes": {"rislin": 1.2, "gevyurctraminer": 1.2, "muskat": 0.6},
        "sparkling": 0.0,
    },
    "celebration": {
        "title": "аперитив и праздник",
        "category": {"Белое": 1.0, "Розовое": 1.0},
        "sweetness": {"brut": 1.5, "extra_brut": 1.2, "brut_nature": 1.0, "semi_dry": 0.3},
        "grapes": {},
        "sparkling": 2.5,
    },
}

LIGHT_GRAPES = ("pino nuar", "aligote", "rislin", "sovinon blan", "kokur", "gamaj")
FULL_GRAPES = ("kaberne sovinon", "saperavi", "krasnostop", "sira", "shiraz", "malbek")
OAK_TOKENS = ("dub", "dubov", "bochk", "barrik", "vyderzh", "tanin")

SWEETNESS_RU = {"brut_nature": "брют натюр", "extra_brut": "экстра брют", "brut": "брют", "dry": "сухое",
                "semi_dry": "полусухое", "semi_sweet": "полусладкое", "sweet": "сладкое"}  # fmt: skip
# множественное число для объяснений «к блюду … подходят белые сухие вина»
CATEGORY_PLURAL = {"Белое": "белые", "Красное": "красные", "Розовое": "розовые", "Оранжевое": "оранжевые"}
SWEETNESS_PLURAL = {"brut_nature": "брют натюр", "extra_brut": "экстра брют", "brut": "брют", "dry": "сухие",
                    "semi_dry": "полусухие", "semi_sweet": "полусладкие", "sweet": "сладкие"}  # fmt: skip


@dataclass(frozen=True)
class SommelierRequest:
    dish: str | None = None  # ключ DISHES
    category: str | None = None  # Белое / Красное / Розовое / Оранжевое
    sweetness: str | None = None  # код сладости
    body: str | None = None  # light | full
    region: str | None = None
    exclude_slugs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Suggestion:
    slug: str
    name: str
    winery: str
    score: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"slug": self.slug, "name": self.name, "winery": self.winery, "score": round(self.score, 3),
                "reasons": self.reasons}  # fmt: skip


def questions() -> list[dict]:
    """Вопросы-кнопки для интерфейса."""
    return [
        {"id": "dish", "title": "К чему подбираем вино?", "options": [{"id": k, "title": v["title"]} for k, v in DISHES.items()]},
        {"id": "category", "title": "Цвет", "options": [{"id": c, "title": c.lower()} for c in ("Белое", "Красное", "Розовое", "Оранжевое")]},
        {"id": "sweetness", "title": "Сладость", "options": [{"id": k, "title": v} for k, v in SWEETNESS_RU.items()]},
        {"id": "body", "title": "Какое вино по ощущению?", "options": [{"id": "light", "title": "лёгкое"}, {"id": "full", "title": "насыщенное"}]},
    ]  # fmt: skip


def _grape_text(card: dict) -> str:
    return " ".join(tokens(" ".join(card.get("grapes", []))))


class Sommelier:
    def __init__(self, cards: dict[str, dict]):
        self.cards = cards

    def suggest(self, request: SommelierRequest, limit: int = 3) -> list[Suggestion]:
        if request.dish is not None and request.dish not in DISHES:
            raise ValueError(f"неизвестное блюдо: {request.dish}")
        rule = DISHES.get(request.dish or "", None)
        results = []
        for card in self.cards.values():
            if card["slug"] in request.exclude_slugs:
                continue
            attributes = card.get("attributes", {})
            if request.category and card["category"] != request.category:
                continue
            if request.sweetness and attributes.get("sweetness") != request.sweetness:
                continue
            if request.region and card.get("region") != request.region:
                continue
            score, reasons = 0.0, []
            grapes = _grape_text(card)
            if rule:
                category_weight = rule["category"].get(card["category"], -1.0)
                sweetness_weight = rule["sweetness"].get(attributes.get("sweetness"), 0.0)
                grape_weight = max((w for g, w in rule["grapes"].items() if g in grapes), default=0.0)
                sparkling_weight = rule["sparkling"] if attributes.get("sparkling") else 0.0
                score += category_weight + sweetness_weight + grape_weight + sparkling_weight
                if category_weight > 0 and (sweetness_weight > 0 or grape_weight > 0):
                    parts = [CATEGORY_PLURAL.get(card["category"], card["category"].lower())]
                    if attributes.get("sweetness"):
                        parts.append(SWEETNESS_PLURAL[attributes["sweetness"]])
                    reasons.append(f"к блюду «{rule['title']}» подходят {' '.join(parts)} вина")
                if grape_weight > 0:
                    reasons.append(f"сорт: {', '.join(card['grapes'])}")
            if request.body:
                description = " ".join(tokens(card.get("description", "")))
                oaky = any(t in description for t in OAK_TOKENS)
                if request.body == "light":
                    light = any(g in grapes for g in LIGHT_GRAPES) and not oaky
                    score += 1.0 if light else -0.5
                    if light:
                        reasons.append("лёгкий стиль")
                elif request.body == "full":
                    full = any(g in grapes for g in FULL_GRAPES) or oaky
                    score += 1.0 if full else -0.5
                    if full:
                        reasons.append("насыщенный стиль" + (", выдержка" if oaky else ""))
            results.append(Suggestion(card["slug"], card["name"], card["winery"], score, reasons))
        # при равных очках порядок не должен зависеть от порядка словаря карточек
        results.sort(key=lambda s: (-s.score, s.name, s.slug))
        chosen, wineries = [], set()
        for suggestion in results:  # разные винодельни, как у аналогов
            if suggestion.winery not in wineries:
                chosen.append(suggestion)
                wineries.add(suggestion.winery)
            if len(chosen) == limit:
                break
        return chosen
