#!/usr/bin/env python3
"""qa/demo_pack.py — генератор демо-паков «Свой Сомелье» (агент F, см. agents/F-qa-demo.md).

Демо-пак = конфигурация, не код (mvp-plan.html, раздел 0): берём slug винодельни из каталога
vines (read-only, /Users/vyacheslavfokin/ClaudeWorkspace/vines/{catalog,ref}) и детерминированно
собираем:

  1. топ-N бутылок винодельни по полноте карточки и рейтингу;
  2. сценарий трёх сцен показа (скан → вопрос сомелье по реальным гастропарам пака →
     «аналог импортного») с ожидаемыми карточками/цитатами;
  3. автопроверку данных (обязательные поля, живость source_url, обоснованность сцен 2 и 3).

Выход: <out-dir>/<winery>/pack.json (машине) и pack.md (человеку).

Использование:
    python demo_pack.py --winery abrau-dyurso
    python demo_pack.py --winery alma-valley --top-n 8 --no-network

Код возврата: 0 — пак валиден (ошибок нет, предупреждения не блокируют);
1 — есть хотя бы одна ошибка (например, обязательное поле пусто у отобранной бутылки,
или дыра в обосновании сцены); 2 — винодельня/данные не найдены (ошибка использования).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    import yaml
except ImportError:  # pragma: no cover — сообщаем внятно, а не трейсбеком импорта
    yaml = None  # type: ignore[assignment]

GENERATOR_NAME = "svoy-somelye-demo-pack"
GENERATOR_VERSION = "1.0.0"

# Скрипт лежит в svoy-somelye/qa/demo_pack.py; данные vines — на два уровня выше svoy-somelye/.
_SCRIPT_DIR = Path(__file__).resolve().parent
_VINES_ROOT = _SCRIPT_DIR.parents[1]
DEFAULT_CATALOG_DIR = _VINES_ROOT / "catalog"
DEFAULT_REF_DIR = _VINES_ROOT / "ref"
DEFAULT_OUT_DIR = _SCRIPT_DIR / "packs"

# Поля-минимум карточки (agents/F-qa-demo.md, DoD генератора). Бутылка без хотя бы одного из
# этих полей НЕ попадает в пак — её нельзя показать инвестору как образец каталога.
MINIMUM_FIELDS: tuple[str, ...] = (
    "name",
    "color",
    "sugar_category",
    "grapes",
    "food_pairings",
    "description",
    "image_url",
)

# Необязательные сигналы «богатства» карточки — тай-брейк и часть composite-скора, не жёсткий
# фильтр. reference_style_matches — единственный сигнал не из source, а из derived.
_OPTIONAL_SOURCE_FIELDS: tuple[str, ...] = (
    "vintage",
    "public_rating",
    "color_in_glass",
    "abv_percent",
    "similar_wine_slugs",
)

RATING_WEIGHT = 0.65
COMPLETENESS_WEIGHT = 0.35

# HTTP-заголовки должны быть latin-1/ASCII (RFC 7230) — кириллица здесь роняла бы каждый
# HEAD-запрос UnicodeEncodeError'ом, поэтому User-Agent намеренно на английском.
POLITE_USER_AGENT = (
    f"{GENERATOR_NAME}/{GENERATOR_VERSION} "
    "(+internal QA tool for 'Svoy Somelye'; polite HEAD check; see agents/F-qa-demo.md)"
)

# Стили эталонного матчера (ref/reference_styles.yaml), которые нешенолог-инвестор скорее
# всего узнает по названию. Используется ТОЛЬКО чтобы сцена 3 звучала эффектнее на показе —
# на автопроверку не влияет: любой найденный стиль одинаково валиден, этот список — просто
# предпочтение при выборе МЕЖДУ несколькими равно верными вариантами. Список общий для любой
# винодельни, ничего специфичного под Абрау в нём нет.
FAMOUS_STYLE_SLUGS: frozenset[str] = frozenset(
    {
        "prosecco",
        "champagne-brut-nv",
        "champagne-rose",
        "cava",
        "chablis",
        "sancerre",
        "riesling-kabinett",
        "pinot-grigio",
        "muscadet",
        "gewurztraminer-alsace",
        "moscato-dasti",
        "sauternes",
        "gruner-veltliner",
        "chianti-classico",
        "brunello",
        "barolo",
        "rioja-reserva",
        "bordeaux-left-bank",
        "bordeaux-right-bank",
        "malbec-mendoza",
        "cabernet-napa",
        "zinfandel-california",
        "burgundy-pinot",
        "beaujolais",
        "chateauneuf-du-pape",
        "valpolicella",
        "port-tawny",
        "madeira",
        "tokaji-aszu",
    }
)

# Натуральные формулировки вопроса сомелье по реальным гастропарам каталога vines (34 разных
# ярлыка на весь каталог из 1978 вин на момент написания — см. reports/f-report.md). Для ярлыка
# вне словаря есть безопасный generic-фолбэк (см. phrase_question) — это подстраховка на
# будущее пополнение каталога, а не признак того, что словарь неполон сегодня.
PAIRING_QUESTIONS: dict[str, str] = {
    "Сыры": "Что взять к сырам?",
    "Твёрдые сыры": "Что взять к твёрдым сырам?",
    "Мягкие сыры": "Что взять к мягким сырам?",
    "Рыба и морепродукты": "Что взять к рыбе и морепродуктам?",
    "Мясо и стейки": "Что взять к мясу и стейкам?",
    "Легкие закуски": "Что взять к лёгким закускам?",
    "Лёгкие закуски": "Что взять к лёгким закускам?",
    "Салаты": "Что взять к салатам?",
    "Блюда из птицы": "Что взять к блюдам из птицы?",
    "Морепродукты": "Что взять к морепродуктам?",
    "Паста": "Что взять к пасте?",
    "Мясное ассорти": "Что взять к мясному ассорти?",
    "BBQ": "Что взять на BBQ?",
    "Выпечка и десерты": "Что взять к выпечке и десертам?",
    "Фрукты": "Что взять к фруктам?",
    "Овощи гриль": "Что взять к овощам гриль?",
    "Паштеты": "Что взять к паштетам?",
    "Десерты": "Что взять к десертам?",
    "Устрицы": "Что взять к устрицам?",
    "Пицца": "Что взять к пицце?",
    "Азиатская кухня": "Что взять к блюдам азиатской кухни?",
    "Острые азиатские блюда": "Что взять к острым азиатским блюдам?",
    "Кухни народов мира": "Что взять к кухне народов мира?",
    "Кавказская кухня": "Что взять к кавказской кухне?",
    "Блюда из рыбы": "Что взять к блюдам из рыбы?",
    "Брускетты": "Что взять к брускеттам?",
    "Несладкая выпечка": "Что взять к несладкой выпечке?",
    "Фастфуд": "Что взять к фастфуду?",
    "Средиземноморская кухня": "Что взять к средиземноморской кухне?",
    "Закуски": "Что взять к закускам?",
    "Аперитив": "Что взять на аперитив?",
    "Дичь": "Что взять к дичи?",
    "Мороженое": "Что взять к мороженому?",
    "Фруктово-ягодные десерты": "Что взять к фруктово-ягодным десертам?",
    "Запеченные овощи": "Что взять к запечённым овощам?",
    "Острое": "Что взять к острому?",
    "Шоколад": "Что взять к шоколаду?",
    "Свежие овощи": "Что взять к свежим овощам?",
    "Русская кухня": "Что взять к русской кухне?",
}


class DemoPackError(Exception):
    """Ошибка использования генератора (винодельня не найдена, битые данные и т.п.).

    Отделена от Issue/ValidationReport: это фатальные проблемы ДО того, как появился
    пак для валидации — main() ловит её и печатает понятное сообщение, а не трейсбек.
    """


# --------------------------------------------------------------------------------
# Утилиты
# --------------------------------------------------------------------------------


def is_nonempty(value: Any) -> bool:
    """Правило «поле непусто» для всего генератора — единое место, один раз."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def missing_minimum_fields(source: dict[str, Any]) -> list[str]:
    return [f for f in MINIMUM_FIELDS if not is_nonempty(source.get(f))]


def _optional_signal_count(source: dict[str, Any], derived: dict[str, Any]) -> tuple[int, int]:
    signals = [is_nonempty(source.get(f)) for f in _OPTIONAL_SOURCE_FIELDS]
    signals.append(is_nonempty(derived.get("reference_style_matches")))
    return sum(signals), len(signals)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------------
# Загрузка данных vines (read-only)
# --------------------------------------------------------------------------------


def load_winery_meta(catalog_dir: Path, slug: str) -> dict[str, Any]:
    path = catalog_dir / "wineries" / f"{slug}.json"
    if not path.exists():
        available = sorted(p.stem for p in (catalog_dir / "wineries").glob("*.json"))
        hint = ", ".join(available[:8]) + ("…" if len(available) > 8 else "")
        raise DemoPackError(
            f"Винодельня '{slug}' не найдена: нет файла {path}.\n"
            f"slug должен совпадать с именем файла в catalog/wineries/ (без .json). "
            f"Примеры доступных: {hint}"
        )
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_wines_for_winery(catalog_dir: Path, slug: str) -> list[dict[str, Any]]:
    """Полный скан catalog/wines/*.json с фильтром по source.winery.

    Сознательно НЕ используем winery.json['source']['wine_slugs'] (это редакционная подборка
    «ключевых» вин винодельни, например у abrau-dyurso — только 12 из фактических 51) и НЕ
    полагаемся на префикс имени файла (у части вин slug не начинается с slug винодельни,
    например locantita-sauvignon-blanc-chardonnay у alma-valley) — только поле
    source.winery, это единственный надёжный ключ связи вина с винодельней в этом каталоге.
    """
    wines_dir = catalog_dir / "wines"
    matched: list[dict[str, Any]] = []
    for path in sorted(wines_dir.glob("*.json")):
        with path.open(encoding="utf-8") as f:
            doc = json.load(f)
        if doc.get("source", {}).get("winery") == slug:
            matched.append(doc)
    return matched


def load_reference_styles(ref_dir: Path) -> dict[str, dict[str, Any]]:
    path = ref_dir / "reference_styles.yaml"
    if not path.exists() or yaml is None:
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {s["slug"]: s for s in data.get("styles", []) if "slug" in s}


# --------------------------------------------------------------------------------
# Скоринг и отбор
# --------------------------------------------------------------------------------


@dataclass
class BottleScore:
    rating: float | None
    rating_component: float
    completeness_component: float
    composite: float


@dataclass
class Bottle:
    rank: int
    wine_id: str
    name: str
    winery_slug: str
    winery_name: str
    region_name: str | None
    color: str
    sugar_category: str
    grapes: list[str]
    food_pairings: list[str]
    description: str
    image_url: str
    vintage: int | None
    public_rating: float | None
    source_url: str
    reference_style_matches: list[str]
    score: BottleScore


def score_wine_doc(doc: dict[str, Any]) -> tuple[BottleScore, list[str]]:
    """Возвращает (score, missing_fields). missing_fields непуст => карточка не участвует
    в отборе вовсе (см. select_top_bottles) — это фильтр, не штраф к рангу."""
    source = doc.get("source", {}) or {}
    derived = doc.get("derived", {}) or {}
    missing = missing_minimum_fields(source)

    raw_rating = source.get("public_rating")
    rating = float(raw_rating) if isinstance(raw_rating, (int, float)) else None
    rating_component = (rating / 5.0) if rating is not None else 0.0

    present, total = _optional_signal_count(source, derived)
    completeness_component = present / total if total else 0.0

    composite = RATING_WEIGHT * rating_component + COMPLETENESS_WEIGHT * completeness_component
    score = BottleScore(
        rating=rating,
        rating_component=round(rating_component, 4),
        completeness_component=round(completeness_component, 4),
        composite=round(composite, 4),
    )
    return score, missing


def _doc_to_bottle(doc: dict[str, Any], rank: int, score: BottleScore) -> Bottle:
    source = doc["source"]
    derived = doc.get("derived", {}) or {}
    provenance = doc.get("provenance", {}) or {}
    return Bottle(
        rank=rank,
        wine_id=doc["slug"],
        name=source["name"],
        winery_slug=source.get("winery", ""),
        winery_name=source.get("winery_name", ""),
        region_name=source.get("region_name"),
        color=source["color"],
        sugar_category=source["sugar_category"],
        grapes=list(source.get("grapes") or []),
        food_pairings=list(source.get("food_pairings") or []),
        description=source["description"],
        image_url=source["image_url"],
        vintage=source.get("vintage"),
        public_rating=score.rating,
        source_url=provenance.get("source_url", ""),
        reference_style_matches=list(derived.get("reference_style_matches") or []),
        score=score,
    )


def select_top_bottles(
    docs: list[dict[str, Any]], top_n: int
) -> tuple[list[Bottle], list[dict[str, Any]]]:
    """Топ-N по composite-скору (рейтинг + полнота карточки), с детерминированным тай-брейком.

    Сортировка: выше composite → выше рейтинг (без рейтинга — в конец) → имя по алфавиту.
    Алфавитный тай-брейк — не эстетика, а воспроизводимость: одинаковый вход обязан давать
    одинаковый пак при повторном прогоне (DoD «3 сухих прогона подряд», mvp-plan §3).
    """
    scored: list[tuple[BottleScore, dict[str, Any]]] = []
    excluded: list[dict[str, Any]] = []
    for doc in docs:
        score, missing = score_wine_doc(doc)
        if missing:
            excluded.append({"wine_id": doc.get("slug", "?"), "missing_fields": missing})
            continue
        scored.append((score, doc))

    scored.sort(
        key=lambda pair: (
            -pair[0].composite,
            -(pair[0].rating if pair[0].rating is not None else -1.0),
            pair[1]["source"]["name"],
        )
    )
    top = scored[:top_n]
    bottles = [_doc_to_bottle(doc, rank=i + 1, score=score) for i, (score, doc) in enumerate(top)]
    return bottles, excluded


# --------------------------------------------------------------------------------
# Сценарий трёх сцен
# --------------------------------------------------------------------------------


def phrase_question(pairing: str) -> str:
    return PAIRING_QUESTIONS.get(pairing, f"Что порекомендуете к сочетанию «{pairing}»?")


def build_scene_scan(bottles: list[Bottle]) -> dict[str, Any]:
    flagship = bottles[0]
    return {
        "scene": 1,
        "title": "Скан этикетки",
        "input_bottle_wine_id": flagship.wine_id,
        "input_description": (
            f"Живая бутылка «{flagship.name}» ({flagship.winery_name}) из демо-набора, "
            f"куплена под этот пак — см. qa/demo-script.md."
        ),
        "sample_scan_text": f"{flagship.name}, {flagship.winery_name}",
        "expected_wine_id": flagship.wine_id,
        "expected_card": {
            "name": flagship.name,
            "winery_name": flagship.winery_name,
            "region_name": flagship.region_name,
            "color": flagship.color,
            "sugar_category": flagship.sugar_category,
            "public_rating": flagship.public_rating,
            "food_pairings": flagship.food_pairings,
            "source_url": flagship.source_url,
        },
        "success_criteria": (
            "Скан -> карточка ≤3 секунды (mvp-plan.html, раздел 0); карточка показывает "
            "рейтинг, гастропары и рабочую ссылку на первоисточник."
        ),
    }


def build_scene_sommelier(bottles: list[Bottle]) -> dict[str, Any]:
    counter: Counter[str] = Counter()
    for b in bottles:
        counter.update(b.food_pairings)

    if not counter:
        return {
            "scene": 2,
            "available": False,
            "reason": "Ни у одной бутылки пака не заполнены food_pairings.",
        }

    # Самая частая гастропара пака; тай-брейк по алфавиту — детерминированность.
    top_pairing = min(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    matching = [b for b in bottles if top_pairing in b.food_pairings]

    def _quote_hint(description: str) -> str:
        description = description.strip()
        return description if len(description) <= 160 else description[:157].rstrip() + "…"

    return {
        "scene": 2,
        "available": True,
        "title": "Вопрос сомелье",
        "question": phrase_question(top_pairing),
        "based_on_real_pairing": top_pairing,
        "pairing_support_count": counter[top_pairing],
        "expected_wine_ids": [b.wine_id for b in matching],
        "expected_citations": [
            {"wine_id": b.wine_id, "name": b.name, "quote_hint": _quote_hint(b.description)}
            for b in matching
        ],
        "success_criteria": (
            "Ответ несёт >=2 цитаты из базы (mvp-plan.html, раздел 0) и явно называет "
            "хотя бы одно из перечисленных вин пака — без выдумки на пустой выдаче."
        ),
        "note": (
            "Точный текст ответа формирует LLM/RAG во время показа (не в этом генераторе) — "
            "здесь зафиксированы факты, обязанные попасть в ответ: сами вина и их реальные "
            "гастропары из каталога, а не дословная цитата."
        ),
    }


def build_scene_analog(
    bottles: list[Bottle], styles_by_slug: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    candidates = [(b, slug) for b in bottles for slug in b.reference_style_matches]
    if not candidates:
        return {
            "scene": 3,
            "available": False,
            "reason": (
                "Ни у одной бутылки пака нет derived.reference_style_matches — "
                "сцену «аналог импортного» на этом паке показать нечем."
            ),
        }

    famous = [pair for pair in candidates if pair[1] in FAMOUS_STYLE_SLUGS]
    chosen_bottle, chosen_slug = (famous or candidates)[0]
    style_meta = styles_by_slug.get(chosen_slug, {})
    style_name = style_meta.get("name", chosen_slug)
    style_country = style_meta.get("country", "?")
    # ref/reference_styles.yaml пишет имена стиля с заглавной буквы (каталожный заголовок:
    # "Шампанское брют без года", "Шабли") — в реплике живого человека это читается как
    # заголовок, а не речь. Понижаем только первую букву — этого достаточно, чтобы
    # "Люблю Шампанское брют без года" стало "Люблю шампанское брют без года", а
    # "Шабли"/"Кьянти" как имена собственные всё равно нормально смотрятся со строчной.
    casual_style_name = style_name[:1].lower() + style_name[1:] if style_name else style_name

    return {
        "scene": 3,
        "available": True,
        "title": "Аналог импортного",
        "user_line": f"Люблю {casual_style_name}",
        "style_slug": chosen_slug,
        "style_name": style_name,
        "style_country": style_country,
        "expected_wine_id": chosen_bottle.wine_id,
        "expected_wine_name": chosen_bottle.name,
        "success_criteria": (
            f"Ответ предлагает российское вино в стиле «{style_name}» ({style_country}) — "
            f"как минимум {chosen_bottle.name} ({chosen_bottle.wine_id}) из пака."
        ),
    }


# --------------------------------------------------------------------------------
# Пак целиком
# --------------------------------------------------------------------------------


@dataclass
class Pack:
    winery: dict[str, Any]
    selection: dict[str, Any]
    bottles: list[Bottle]
    scenario: dict[str, Any]
    generated_at: str
    generator: dict[str, str] = field(
        default_factory=lambda: {"name": GENERATOR_NAME, "version": GENERATOR_VERSION}
    )


def build_pack(
    winery_slug: str,
    *,
    catalog_dir: Path = DEFAULT_CATALOG_DIR,
    ref_dir: Path = DEFAULT_REF_DIR,
    top_n: int = 8,
) -> Pack:
    winery_meta = load_winery_meta(catalog_dir, winery_slug)
    docs = load_wines_for_winery(catalog_dir, winery_slug)
    if not docs:
        raise DemoPackError(
            f"У винодельни '{winery_slug}' нет ни одного вина в catalog/wines "
            f"(поле source.winery нигде не совпало со slug винодельни)."
        )

    bottles, excluded = select_top_bottles(docs, top_n)
    if not bottles:
        raise DemoPackError(
            f"У винодельни '{winery_slug}' есть {len(docs)} вин, но ни одно не прошло "
            f"минимальный набор полей {MINIMUM_FIELDS} — пак собрать не из чего."
        )
    if len(bottles) < top_n:
        note = (
            f"Запрошено {top_n}, но у винодельни только {len(bottles)} годных карточек "
            f"из {len(docs)} — пак собран из того, что есть."
        )
    else:
        note = None

    styles_by_slug = load_reference_styles(ref_dir)
    scenario = {
        "scene_1_scan": build_scene_scan(bottles),
        "scene_2_sommelier": build_scene_sommelier(bottles),
        "scene_3_analog": build_scene_analog(bottles, styles_by_slug),
    }

    winery_source = winery_meta.get("source", {}) or {}
    winery_provenance = winery_meta.get("provenance", {}) or {}
    winery_info = {
        "slug": winery_slug,
        "name": winery_source.get("name", winery_slug),
        "region_name": winery_source.get("region_name"),
        "founded_year": winery_source.get("founded_year"),
        "source_url": winery_provenance.get("source_url", ""),
        "wine_count_in_catalog": len(docs),
    }
    selection = {
        "requested_top_n": top_n,
        "selected_count": len(bottles),
        "scorable_count": len(docs) - len(excluded),
        "excluded_incomplete": excluded,
        "note": note,
        "methodology": (
            f"composite = {RATING_WEIGHT}×(рейтинг/5, 0 если рейтинга нет) + "
            f"{COMPLETENESS_WEIGHT}×(доля заполненных необязательных полей из "
            f"{list(_OPTIONAL_SOURCE_FIELDS) + ['derived.reference_style_matches']}). "
            f"Карточки без обязательных полей {MINIMUM_FIELDS} исключаются целиком (см. "
            f"excluded_incomplete), не понижаются в ранге. Равенство composite решается по "
            f"рейтингу, затем по имени (детерминированность между прогонами)."
        ),
    }

    return Pack(
        winery=winery_info,
        selection=selection,
        bottles=bottles,
        scenario=scenario,
        generated_at=_utcnow_iso(),
    )


# --------------------------------------------------------------------------------
# Автопроверка
# --------------------------------------------------------------------------------


@dataclass
class Issue:
    level: str  # "error" | "warning"
    code: str
    message: str
    wine_id: str | None = None


@dataclass
class ValidationReport:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    checked_urls: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


HeadFn = Callable[[str, float], int]


def _polite_head(url: str, timeout: float) -> int:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": POLITE_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — HEAD, вежливо
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _iter_unique_urls(pack: Pack) -> Iterable[tuple[str, str | None]]:
    seen: set[str] = set()
    if pack.winery["source_url"]:
        seen.add(pack.winery["source_url"])
        yield pack.winery["source_url"], None
    for bottle in pack.bottles:
        if not bottle.source_url or bottle.source_url in seen:
            continue
        seen.add(bottle.source_url)
        yield bottle.source_url, bottle.wine_id


def validate_pack(
    pack: Pack,
    *,
    check_network: bool = True,
    timeout: float = 5.0,
    polite_delay: float = 0.5,
    head_fn: HeadFn | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ValidationReport:
    """Три независимые проверки данных пака — ни одна не доверяет тому, что отбор уже
    "сделал всё правильно": каждая перечитывает исходные данные бутылки заново.

    1. Обязательные поля непусты у каждой отобранной бутылки (это ловит фикстуру со
       сломанной карточкой в тестах — см. qa/test_demo_pack.py).
    2. source_url каждой бутылки (и винодельни) отвечает 200 на вежливый HEAD, с паузой
       между уникальными запросами и дедупликацией. Сетевые проблемы -> warning, не error:
       генерация пака не должна падать из-за таймаута портала, но обязана быть заметна.
    3. Сцены 2 и 3 сценария действительно обоснованы данными пака (гастропара сцены 2
       реально встречается у бутылки; стиль сцены 3 реально есть в derived бутылки).
    """
    issues: list[Issue] = []

    for bottle in pack.bottles:
        for field_name, value in (
            ("name", bottle.name),
            ("color", bottle.color),
            ("sugar_category", bottle.sugar_category),
            ("grapes", bottle.grapes),
            ("food_pairings", bottle.food_pairings),
            ("description", bottle.description),
            ("image_url", bottle.image_url),
        ):
            if not is_nonempty(value):
                issues.append(
                    Issue(
                        "error",
                        "empty_required_field",
                        f"Бутылка {bottle.wine_id}: пустое обязательное поле '{field_name}'.",
                        bottle.wine_id,
                    )
                )

    checked_urls = 0
    if check_network:
        head = head_fn or _polite_head
        for i, (url, wine_id) in enumerate(_iter_unique_urls(pack)):
            if i > 0:
                sleep_fn(polite_delay)
            checked_urls += 1
            try:
                status = head(url, timeout)
            except Exception as exc:  # сеть недоступна/таймаут — не роняем пак, но не молчим
                issues.append(
                    Issue(
                        "warning",
                        "source_url_unreachable",
                        f"{url}: HEAD не выполнен ({exc!r}).",
                        wine_id,
                    )
                )
                continue
            if status != 200:
                issues.append(
                    Issue(
                        "warning",
                        "source_url_bad_status",
                        f"{url}: HEAD вернул {status}, ожидали 200.",
                        wine_id,
                    )
                )
    else:
        issues.append(
            Issue("warning", "network_check_skipped", "Проверка source_url отключена (--no-network).")
        )

    scene2 = pack.scenario.get("scene_2_sommelier", {})
    if scene2.get("available"):
        pairing = scene2["based_on_real_pairing"]
        if not any(pairing in b.food_pairings for b in pack.bottles):
            issues.append(
                Issue(
                    "error",
                    "scene2_pairing_not_grounded",
                    f"Гастропара «{pairing}» сцены 2 не найдена ни у одной бутылки пака.",
                )
            )

    scene3 = pack.scenario.get("scene_3_analog", {})
    if scene3.get("available"):
        wine_id = scene3["expected_wine_id"]
        style_slug = scene3["style_slug"]
        bottle = next((b for b in pack.bottles if b.wine_id == wine_id), None)
        if bottle is None or style_slug not in bottle.reference_style_matches:
            issues.append(
                Issue(
                    "error",
                    "scene3_analog_not_grounded",
                    f"Стиль «{style_slug}» не найден в derived.reference_style_matches "
                    f"вина {wine_id} (сцена 3).",
                    wine_id,
                )
            )

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    return ValidationReport(errors=errors, warnings=warnings, checked_urls=checked_urls)


# --------------------------------------------------------------------------------
# Рендеринг
# --------------------------------------------------------------------------------


def _issue_dict(issue: Issue) -> dict[str, Any]:
    return {"level": issue.level, "code": issue.code, "message": issue.message, "wine_id": issue.wine_id}


def pack_to_json_dict(pack: Pack, report: ValidationReport) -> dict[str, Any]:
    data = asdict(pack)
    data["validation"] = {
        "ok": report.ok,
        "checked_urls": report.checked_urls,
        "errors": [_issue_dict(i) for i in report.errors],
        "warnings": [_issue_dict(i) for i in report.warnings],
    }
    return data


def _bottle_row(b: Bottle) -> str:
    rating = f"{b.public_rating:.1f}" if b.public_rating is not None else "—"
    grapes = ", ".join(b.grapes) or "—"
    pairings = ", ".join(b.food_pairings) or "—"
    return (
        f"| {b.rank} | [{b.name}]({b.source_url}) | {b.color} / {b.sugar_category} | "
        f"{rating} | {grapes} | {pairings} |"
    )


def render_pack_md(pack: Pack, report: ValidationReport) -> str:
    w = pack.winery
    lines: list[str] = []
    lines.append(f"# Демо-пак: {w['name']}")
    lines.append("")
    lines.append(
        f"Сгенерировано: {pack.generated_at} · {pack.generator['name']} v{pack.generator['version']} · "
        f"slug `{w['slug']}`"
    )
    lines.append("")
    lines.append("## Винодельня")
    lines.append("")
    lines.append(f"- Название: {w['name']}")
    if w.get("region_name"):
        lines.append(f"- Регион: {w['region_name']}")
    if w.get("founded_year"):
        lines.append(f"- Основана: {w['founded_year']}")
    lines.append(f"- Вин в каталоге: {w['wine_count_in_catalog']} (пригодных к отбору: {pack.selection['scorable_count']}, исключено по неполноте: {len(pack.selection['excluded_incomplete'])})")
    if w.get("source_url"):
        lines.append(f"- Первоисточник: {w['source_url']}")
    if pack.selection.get("note"):
        lines.append(f"- Замечание: {pack.selection['note']}")
    lines.append("")
    lines.append(f"## Топ-{len(pack.bottles)} бутылок (по рейтингу и полноте карточки)")
    lines.append("")
    lines.append("| # | Вино | Цвет / сахар | Рейтинг | Сорта | Гастропары |")
    lines.append("|---|------|--------------|---------|-------|------------|")
    for b in pack.bottles:
        lines.append(_bottle_row(b))
    lines.append("")
    lines.append(f"Методология: {pack.selection['methodology']}")
    if pack.selection["excluded_incomplete"]:
        lines.append("")
        lines.append("Исключены из отбора (не хватает обязательных полей):")
        for item in pack.selection["excluded_incomplete"]:
            lines.append(f"- `{item['wine_id']}` — нет: {', '.join(item['missing_fields'])}")

    lines.append("")
    lines.append("## Сценарий показа (три сцены)")

    s1 = pack.scenario["scene_1_scan"]
    lines.append("")
    lines.append("### Сцена 1 — Скан")
    lines.append("")
    lines.append(f"- Бутылка: **{s1['expected_card']['name']}** ({s1['expected_card']['winery_name']}), `{s1['input_bottle_wine_id']}`")
    lines.append(f"- {s1['input_description']}")
    lines.append(f"- Текст на случай сканера текстом / ручного ввода: «{s1['sample_scan_text']}»")
    lines.append(f"- Ожидаемая карточка: {s1['expected_card']['color']}, {s1['expected_card']['sugar_category']}, рейтинг {s1['expected_card']['public_rating']}, гастропары: {', '.join(s1['expected_card']['food_pairings'])}")
    lines.append(f"- Первоисточник карточки: {s1['expected_card']['source_url']}")
    lines.append(f"- Критерий успеха: {s1['success_criteria']}")

    s2 = pack.scenario["scene_2_sommelier"]
    lines.append("")
    lines.append("### Сцена 2 — Вопрос сомелье")
    lines.append("")
    if s2.get("available"):
        lines.append(f"- Вопрос (по реальной гастропаре пака, встречается у {s2['pairing_support_count']} из {len(pack.bottles)} бутылок): «{s2['question']}»")
        lines.append(f"- Гастропара: {s2['based_on_real_pairing']}")
        lines.append("- Ожидаемые вина в ответе (>=1 из перечисленных, ответ обязан нести >=2 цитаты):")
        for c in s2["expected_citations"]:
            lines.append(f"  - `{c['wine_id']}` — {c['name']}: «{c['quote_hint']}»")
        lines.append(f"- Критерий успеха: {s2['success_criteria']}")
        lines.append(f"- {s2['note']}")
    else:
        lines.append(f"- Недоступна: {s2['reason']}")

    s3 = pack.scenario["scene_3_analog"]
    lines.append("")
    lines.append("### Сцена 3 — Аналог импортного")
    lines.append("")
    if s3.get("available"):
        lines.append(f"- Реплика пользователя: «{s3['user_line']}»")
        lines.append(f"- Эталонный стиль: {s3['style_name']} ({s3['style_country']}), slug `{s3['style_slug']}`")
        lines.append(f"- Ожидаемое вино пака: **{s3['expected_wine_name']}** (`{s3['expected_wine_id']}`)")
        lines.append(f"- Критерий успеха: {s3['success_criteria']}")
    else:
        lines.append(f"- Недоступна: {s3['reason']}")

    lines.append("")
    lines.append("## Автопроверка")
    lines.append("")
    lines.append(f"- Обязательные поля непусты у всех {len(pack.bottles)} бутылок: {'OK' if not any(i.code == 'empty_required_field' for i in report.errors) else 'ОШИБКА'}")
    lines.append(f"- source_url отвечает 200 на вежливый HEAD: проверено {report.checked_urls} уникальных ссылок, предупреждений: {sum(1 for i in report.warnings if i.code in ('source_url_bad_status', 'source_url_unreachable'))}")
    lines.append(f"- Сцена 2 опирается на реальную гастропару пака: {'OK' if not any(i.code == 'scene2_pairing_not_grounded' for i in report.errors) else 'ОШИБКА'}")
    lines.append(f"- Сцена 3 — аналог подтверждён в derived.reference_style_matches: {'OK' if not any(i.code == 'scene3_analog_not_grounded' for i in report.errors) else 'ОШИБКА'}")
    lines.append("")
    if report.errors:
        lines.append(f"**Вердикт: ЕСТЬ ОШИБКИ ({len(report.errors)}) — пак НЕ готов к показу.**")
        for issue in report.errors:
            lines.append(f"- ERROR [{issue.code}] {issue.message}")
    else:
        lines.append("**Вердикт: пак готов к показу.**")
    if report.warnings:
        lines.append("")
        lines.append("Предупреждения (не блокируют, но стоит посмотреть):")
        for issue in report.warnings:
            lines.append(f"- WARNING [{issue.code}] {issue.message}")
    lines.append("")
    return "\n".join(lines)


def write_pack(pack: Pack, report: ValidationReport, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "pack.json"
    md_path = out_dir / "pack.md"
    json_path.write_text(
        json.dumps(pack_to_json_dict(pack, report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_pack_md(pack, report), encoding="utf-8")
    return json_path, md_path


# --------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--winery", required=True, help="slug винодельни, как в catalog/wineries/<slug>.json")
    parser.add_argument("--top-n", type=int, default=8, help="сколько бутылок отобрать (по умолчанию 8)")
    parser.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG_DIR)
    parser.add_argument("--ref-dir", type=Path, default=DEFAULT_REF_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="корень для <out-dir>/<winery>/pack.{md,json}")
    parser.add_argument("--no-network", action="store_true", help="не делать HEAD-запросы к source_url (офлайн/тесты)")
    parser.add_argument("--polite-delay", type=float, default=0.5, help="пауза между HEAD-запросами, сек")
    parser.add_argument("--timeout", type=float, default=5.0, help="таймаут одного HEAD-запроса, сек")
    parser.add_argument("--strict", action="store_true", help="ненулевой код возврата и на предупреждениях тоже")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    try:
        pack = build_pack(
            args.winery,
            catalog_dir=args.catalog_dir,
            ref_dir=args.ref_dir,
            top_n=args.top_n,
        )
    except DemoPackError as exc:
        print(f"[demo_pack] ОШИБКА: {exc}", file=sys.stderr)
        return 2

    report = validate_pack(
        pack,
        check_network=not args.no_network,
        timeout=args.timeout,
        polite_delay=args.polite_delay,
    )

    out_dir = args.out_dir / args.winery
    json_path, md_path = write_pack(pack, report, out_dir)

    print(f"[demo_pack] Винодельня: {pack.winery['name']} ({args.winery})")
    print(f"[demo_pack] Отобрано бутылок: {len(pack.bottles)} / запрошено {args.top_n} (в каталоге {pack.winery['wine_count_in_catalog']})")
    for scene_key in ("scene_1_scan", "scene_2_sommelier", "scene_3_analog"):
        scene = pack.scenario[scene_key]
        status = "готова" if scene.get("available", True) else f"недоступна ({scene.get('reason')})"
        print(f"[demo_pack] Сцена {scene['scene']} «{scene.get('title', scene_key)}»: {status}")
    print(f"[demo_pack] Автопроверка: {len(report.errors)} ошибок, {len(report.warnings)} предупреждений (проверено ссылок: {report.checked_urls})")
    for issue in report.errors:
        print(f"[demo_pack]   ERROR [{issue.code}] {issue.message}", file=sys.stderr)
    for issue in report.warnings:
        print(f"[demo_pack]   WARNING [{issue.code}] {issue.message}")
    print(f"[demo_pack] Записано: {md_path}")
    print(f"[demo_pack]           {json_path}")

    if report.errors:
        return 1
    if args.strict and report.warnings:
        return 1
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
