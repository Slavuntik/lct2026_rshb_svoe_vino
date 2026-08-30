#!/usr/bin/env python3
"""qa/demo_pack.py — генератор демо-паков «Свой Сомелье» (агент F, см. agents/F-qa-demo.md).

Демо-пак = конфигурация, не код (mvp-plan.html, раздел 0): берём slug винодельни из каталога
vines (read-only, /Users/vyacheslavfokin/ClaudeWorkspace/vines/{catalog,ref}) и детерминированно
собираем:

  1. топ-N бутылок винодельни по полноте карточки и рейтингу, у каждой — deep-link
     /app/wine/<slug> (mvp-plan.html: страховка от плохого света на сцене скана);
  2. сценарий трёх сцен показа (скан → вопрос сомелье по реальным гастропарам пака →
     «аналог импортного») с ожидаемыми карточками/цитатами, плюс refusal_probe —
     верифицированный «момент доверия» из голд-сета калибровки RAG (packages/rag/eval/
     goldset.jsonl, type=refusal), не придуманный генератором;
  3. автопроверку данных (обязательные поля, живость source_url, обоснованность сцен 2 и 3,
     и — если apps/api поднят локально — что refusal_probe реально отклоняется живым API).

Выход: <out-dir>/<winery>/pack.json (машине) и pack.md (человеку).

Использование:
    python demo_pack.py --winery abrau-dyurso
    python demo_pack.py --winery alma-valley --top-n 8 --no-network --no-refusal-check

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
_REPO_ROOT = _SCRIPT_DIR.parent
_VINES_ROOT = _SCRIPT_DIR.parents[1]
DEFAULT_CATALOG_DIR = _VINES_ROOT / "catalog"
DEFAULT_REF_DIR = _VINES_ROOT / "ref"
DEFAULT_OUT_DIR = _SCRIPT_DIR / "packs"
# Голд-сет калибровки индекса агента A (packages/rag/ — читаем, не пишем, чужая зона) —
# единственный источник вопросов для refusal_probe (ревью 03: импровизированный вопрос
# «какое вино снижает давление?» отсечку НЕ проходит, там есть слово «вино» — брать нужно
# только проверенные калибровкой примеры, не придумывать свои).
DEFAULT_GOLDSET_PATH = _REPO_ROOT / "packages" / "rag" / "eval" / "goldset.jsonl"
DEFAULT_API_URL = "http://localhost:8000"

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


# Среди верифицированных refusal-вопросов голд-сета предпочитаем те, что по форме похожи на
# "спроси эксперта, но не по вину" ("посоветуй"/рекомендательные) — это сильнее звучит на
# показе как разговор с сомелье, который честно не притворяется экспертом вне вина, чем
# заведомо чужеродные вопросы (карбюратор, wifi-роутер). Список — ПРЕДПОЧТЕНИЕ выбора МЕЖДУ
# равно верными вариантами голд-сета, не альтернативный источник вопросов; живая проверка
# (validate_pack, check_refusal_live) — независимая страховка от дрейфа калибровки/индекса
# на случай, если предпочтение всё же устареет.
#
# ВАЖНО (ревью 03, эмпирически на индексе 20260828.1 после B v0.3.3): не все рекомендательные
# формулировки голд-сета одинаково надёжны — «Как приготовить борщ…», «Посоветуй хорошую
# книгу…» и «...фильм ужасов…» на этом индексе уже НЕ рефьюзятся (находят цитату в статьях
# базы знаний про сочетания с книгой/фильмом за бокалом) — реальный прогон демо-пака поймал
# это через `refusal_probe_not_refused`, что и есть смысл проверки. Ниже — только вопросы,
# перепроверенные вручную и стабильно (дважды подряд) отклоняемые на момент правки; список
# может снова устареть, живая проверка остаётся источником правды, не этот порядок.
_REFUSAL_PROBE_PREFERENCE: tuple[str, ...] = (
    "Порекомендуй интересный сериал на выходные.",
    "Когда следующий чемпионат мира по футболу?",
    "Как настроить wifi роутер дома?",
    "Посоветуй, где купить кроссовки для бега.",
)


def load_refusal_probe(goldset_path: Path) -> dict[str, Any] | None:
    """«Момент доверия» демо (qa/demo-script.md) — вопрос вне темы вина, на который сомелье
    обязан честно отказать, а не выдумать ответ. Источник — ЕДИНСТВЕННЫЙ: голд-сет
    калибровки refusal-порога индекса (packages/rag/eval/goldset.jsonl, type=refusal,
    agents/A-rag.md) — сюда уже отобраны вопросы, на которых калибровка реально проверила
    отказ, а не просто "выглядит неуместно". Ревью 03: импровизация («какое вино снижает
    давление?») не проходит порог — там есть слово «вино», отсечка резонирует с ним.

    Возвращает None, если голд-сета нет/пуст — вызывающий код (build_pack) должен НЕ падать,
    а честно пометить сцену недоступной (тот же паттерн, что и scene_2/scene_3 при нехватке
    данных), см. `available` в pack.json.
    """
    if not goldset_path.exists():
        return None
    refusal_questions: list[str] = []
    with goldset_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("type") == "refusal" and is_nonempty(entry.get("q")):
                refusal_questions.append(entry["q"])
    if not refusal_questions:
        return None

    # Детерминированный выбор (не random.choice) — обязателен для «3 прогона байт-в-байт».
    chosen = next((q for q in _REFUSAL_PROBE_PREFERENCE if q in refusal_questions), None)
    if chosen is None:
        chosen = sorted(refusal_questions)[0]

    return {
        "question": chosen,
        "source": (
            "packages/rag/eval/goldset.jsonl (type=refusal, верифицировано калибровкой "
            "refusal-порога индекса, agents/A-rag.md — не придумано генератором пака)"
        ),
        "total_refusal_examples_in_goldset": len(refusal_questions),
        "success_criteria": (
            "Честный отказ (SSE-событие type=refusal), ни одной выдуманной цитаты — "
            "демонстрирует, что сомелье не притворяется экспертом вне вина, даже на смежную "
            "бытовую тему."
        ),
    }


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
    deep_link: str


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


def deep_link_for(wine_id: str) -> str:
    """Путь клиента на карточку вина (apps/web/src/router.tsx + AppShell.tsx: маршрут
    /app/wine/:wineId) — mvp-plan.html, раздел 0: «deep-link'и на карточки как страховка от
    плохого света на сцене [скана]». Путь, не полный URL — хост зависит от канала показа
    (демо-iPhone на staging, веб-версия с лендинга, localhost на прогоне) и генератору
    неизвестен; см. qa/demo-script.md, раздел про план Б скана."""
    return f"/app/wine/{wine_id}"


def _doc_to_bottle(doc: dict[str, Any], rank: int, score: BottleScore) -> Bottle:
    source = doc["source"]
    derived = doc.get("derived", {}) or {}
    provenance = doc.get("provenance", {}) or {}
    wine_id = doc["slug"]
    return Bottle(
        rank=rank,
        wine_id=wine_id,
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
        deep_link=deep_link_for(wine_id),
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


def _ru_wine_count(n: int) -> str:
    """Число + правильная форма «вино» (1 вино / 2 вина / 5 вин / 14 вин / 21 вино...)."""
    n_abs = abs(n)
    if n_abs % 100 in (11, 12, 13, 14):
        word = "вин"
    elif n_abs % 10 == 1:
        word = "вино"
    elif n_abs % 10 in (2, 3, 4):
        word = "вина"
    else:
        word = "вин"
    return f"{n} {word}"


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
        "deep_link_fallback": flagship.deep_link,
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
            "рейтинг, гастропары и рабочую ссылку на первоисточник. Если сканер подводит "
            "(плохой свет — главный технический риск демо, раздел 5 плана) — открыть "
            "карточку напрямую по deep_link_fallback, не пересканировать на глазах у гостя."
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
    bottles: list[Bottle],
    styles_by_slug: dict[str, dict[str, Any]],
    winery_docs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Сцена 3 — «аналог импортного».

    Достижимый инвариант (по итогам приёмочного прогона волны 3, см. reports/f-report.md):
    живой `analog_for_style` фильтрует по цвету/игристости СТРОЖЕ, чем статический per-вина
    `derived.reference_style_matches`, на котором держится этот генератор (у него нет
    доступа к живому RAG) — конкретная бутылка пака иногда не совпадает с тем, что реально
    вернёт `/analogs`, хотя стиль и винодельня — те же. Поэтому здесь фиксируется не «эта
    ТОЧНО бутылка», а «эта винодельня ТОЧНО имеет вино(-а) этого стиля» — посчитано по ВСЕЙ
    винодельне (`winery_docs`), не только по топ-8 пака: чем больше вин винодельни этого
    стиля существует в каталоге, тем увереннее, что живая выдача найдёт хотя бы одно из них.
    Одна конкретная бутылка (`illustrative_wine_id`) остаётся для реплики и текста показа,
    но не как строгое условие успеха сцены.
    """
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

    winery_slug = chosen_bottle.winery_slug
    winery_name = chosen_bottle.winery_name
    winery_style_wine_ids = [
        doc["slug"]
        for doc in winery_docs
        if chosen_slug in ((doc.get("derived") or {}).get("reference_style_matches") or [])
    ]

    return {
        "scene": 3,
        "available": True,
        "title": "Аналог импортного",
        "user_line": f"Люблю {casual_style_name}",
        "style_slug": chosen_slug,
        "style_name": style_name,
        "style_country": style_country,
        "winery_slug": winery_slug,
        "winery_name": winery_name,
        "illustrative_wine_id": chosen_bottle.wine_id,
        "illustrative_wine_name": chosen_bottle.name,
        "winery_style_wine_ids": winery_style_wine_ids,
        "winery_style_wine_count": len(winery_style_wine_ids),
        "success_criteria": (
            f"Ответ предлагает российское вино винодельни {winery_name} в стиле «{style_name}» "
            f"({style_country}) — в каталоге винодельни {_ru_wine_count(len(winery_style_wine_ids))} "
            f"этого стиля (например, {chosen_bottle.name} из пака). Живая RAG-выдача вправе назвать "
            f"любое из них — важно совпадение по стилю и винодельне, не байт-в-байт с конкретной "
            f"бутылкой пака (фильтр стиля у живого RAG строже статического per-вина списка)."
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
    refusal_probe: dict[str, Any] | None = None
    generator: dict[str, str] = field(
        default_factory=lambda: {"name": GENERATOR_NAME, "version": GENERATOR_VERSION}
    )


def build_pack(
    winery_slug: str,
    *,
    catalog_dir: Path = DEFAULT_CATALOG_DIR,
    ref_dir: Path = DEFAULT_REF_DIR,
    top_n: int = 8,
    goldset_path: Path = DEFAULT_GOLDSET_PATH,
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
        "scene_3_analog": build_scene_analog(bottles, styles_by_slug, docs),
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
        refusal_probe=load_refusal_probe(goldset_path),
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


RefusalProbeFn = Callable[[str, str, float], dict[str, Any]]


def _check_refusal_probe_live(question: str, api_url: str, timeout: float) -> dict[str, Any]:
    """Настоящий POST /auth/guest + POST /chat против живого apps/api (не TestClient —
    честный HTTP, как и вежливые HEAD выше). Возвращает
    {"status": "confirmed_refused" | "confirmed_not_refused" | "unreachable", "detail": str}.

    "confirmed_not_refused" — это ERROR уровня пака: если вопрос из голд-сета калибровки
    вдруг перестал рефьюзиться на текущем индексе (индекс мог обновиться), refusal_probe
    больше не годится для demo-script.md и требует новой калибровки/выбора, а не тихого
    расхождения с реальностью на показе.
    """
    guest_body = json.dumps({"age_confirmed": True, "consent_version": "demo-pack-refusal-probe"}).encode("utf-8")
    guest_req = urllib.request.Request(
        f"{api_url}/v1/auth/guest",
        data=guest_body,
        headers={"Content-Type": "application/json", "User-Agent": POLITE_USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(guest_req, timeout=timeout) as resp:  # noqa: S310
            token = json.loads(resp.read().decode("utf-8"))["access_token"]
    except Exception as exc:
        return {"status": "unreachable", "detail": f"{api_url}: не удалось получить гостевой токен ({exc!r})."}

    chat_body = json.dumps({"message": question}).encode("utf-8")
    chat_req = urllib.request.Request(
        f"{api_url}/v1/chat",
        data=chat_body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
            "User-Agent": POLITE_USER_AGENT,
        },
        method="POST",
    )
    try:
        saw_refusal = False
        saw_citation = False
        with urllib.request.urlopen(chat_req, timeout=timeout) as resp:  # noqa: S310
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[len("data:") :].strip())
                event_type = event.get("type")
                if event_type == "refusal":
                    saw_refusal = True
                elif event_type == "citation":
                    saw_citation = True
                elif event_type == "done":
                    break
    except Exception as exc:
        return {"status": "unreachable", "detail": f"{api_url}: /chat не ответил ({exc!r})."}

    if saw_refusal and not saw_citation:
        return {"status": "confirmed_refused", "detail": "живой API честно отказал, ни одной цитаты."}
    return {
        "status": "confirmed_not_refused",
        "detail": (
            f"живой API НЕ отказал на вопрос из голд-сета (refusal={saw_refusal}, "
            f"citation={saw_citation}) — вопрос больше не годится как refusal_probe, "
            f"нужна повторная выборка из packages/rag/eval/goldset.jsonl."
        ),
    }


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
    check_refusal_live: bool = True,
    api_url: str = DEFAULT_API_URL,
    api_timeout: float = 20.0,
    refusal_probe_fn: RefusalProbeFn | None = None,
) -> ValidationReport:
    """Четыре независимые проверки данных пака — ни одна не доверяет тому, что отбор уже
    "сделал всё правильно": каждая перечитывает исходные данные бутылки заново.

    1. Обязательные поля непусты у каждой отобранной бутылки (это ловит фикстуру со
       сломанной карточкой в тестах — см. qa/test_demo_pack.py).
    2. source_url каждой бутылки (и винодельни) отвечает 200 на вежливый HEAD, с паузой
       между уникальными запросами и дедупликацией. Сетевые проблемы -> warning, не error:
       генерация пака не должна падать из-за таймаута портала, но обязана быть заметна.
    3. Сцены 2 и 3 сценария действительно обоснованы данными пака (гастропара сцены 2
       реально встречается у бутылки; стиль сцены 3 реально есть в derived бутылки).
    4. refusal_probe (если голд-сет калибровки нашёлся) реально отклоняется ЖИВЫМ apps/api —
       не эмуляция, настоящий HTTP на api_url (дефолт localhost:8000, как и vite-proxy).
       Сервер недоступен -> warning (это ожидаемо на большинстве прогонов `make demo-pack`,
       где apps/api не поднят) — но НЕ falls back на "просто доверять" тихо: сам факт "не
       проверено вживую" виден в отчёте, а не спрятан.
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
        illustrative_id = scene3["illustrative_wine_id"]
        style_slug = scene3["style_slug"]
        # (1) Иллюстративная бутылка (топ-8 пака, для реплики/текста показа) — быстрый
        # повторный чек по уже загруженному Bottle, как и раньше.
        bottle = next((b for b in pack.bottles if b.wine_id == illustrative_id), None)
        if bottle is None or style_slug not in bottle.reference_style_matches:
            issues.append(
                Issue(
                    "error",
                    "scene3_illustrative_wine_not_grounded",
                    f"Стиль «{style_slug}» не найден в derived.reference_style_matches "
                    f"иллюстративного вина {illustrative_id} (сцена 3).",
                    illustrative_id,
                )
            )
        # (2) Достижимый инвариант сцены (не «эта точная бутылка», а «у винодельни ЕСТЬ вино
        # этого стиля» — см. докстринг build_scene_analog): у живого RAG фильтр по стилю
        # строже статического списка per-вина, поэтому именно это, а не конкретный wine_id,
        # разумно требовать от генератора, который не видит живой RAG.
        if scene3.get("winery_style_wine_count", 0) < 1:
            issues.append(
                Issue(
                    "error",
                    "scene3_style_not_grounded_in_winery",
                    f"Ни одно вино винодельни {scene3.get('winery_slug')} не несёт стиль "
                    f"«{style_slug}» в derived.reference_style_matches (сцена 3) — "
                    f"достижимый инвариант («стиль есть у винодельни») не выполняется.",
                )
            )

    if pack.refusal_probe is None:
        issues.append(
            Issue(
                "warning",
                "refusal_probe_unavailable",
                f"Голд-сет калибровки ({DEFAULT_GOLDSET_PATH}) не найден или пуст — "
                f"refusal_probe не сформирован, «момент доверия» демо-скрипта недоступен.",
            )
        )
    elif check_refusal_live:
        probe_fn = refusal_probe_fn or _check_refusal_probe_live
        result = probe_fn(pack.refusal_probe["question"], api_url, api_timeout)
        if result["status"] == "confirmed_not_refused":
            issues.append(Issue("error", "refusal_probe_not_refused", result["detail"]))
        elif result["status"] == "unreachable":
            issues.append(Issue("warning", "refusal_probe_live_check_skipped", result["detail"]))
        # "confirmed_refused" — успех, никакого issue не заводим (тот же принцип, что и для
        # 200 на source_url: issues — это про проблемы, не про подтверждения).
    else:
        issues.append(
            Issue(
                "warning",
                "refusal_probe_live_check_disabled",
                "Живая проверка refusal_probe отключена (--no-refusal-check).",
            )
        )

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    return ValidationReport(errors=errors, warnings=warnings, checked_urls=checked_urls)


# --------------------------------------------------------------------------------
# Рендеринг
# --------------------------------------------------------------------------------


def refusal_probe_status_text(report: ValidationReport) -> str:
    """Единая логика статуса refusal_probe для CLI-вывода и pack.md — три различимых
    warning/error-кода, каждый со своим текстом, ни один не спутать с успехом (найденная на
    этой же фиче ошибка: сперва оба места отдельно проверяли только `not_refused` и
    `live_check_skipped`, забыв про `live_check_disabled` — из-за этого --no-refusal-check
    ошибочно печатал «подтверждён живым API»)."""
    if any(i.code == "refusal_probe_not_refused" for i in report.errors):
        return "ОШИБКА — живой API НЕ отказал"
    if any(i.code == "refusal_probe_live_check_skipped" for i in report.warnings):
        return "не проверено вживую (apps/api недоступен на момент генерации)"
    if any(i.code == "refusal_probe_live_check_disabled" for i in report.warnings):
        return "не проверено вживую (--no-refusal-check)"
    return "подтверждён живым API"


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
        f"{rating} | {grapes} | {pairings} | `{b.deep_link}` |"
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
    lines.append("| # | Вино | Цвет / сахар | Рейтинг | Сорта | Гастропары | Deep-link |")
    lines.append("|---|------|--------------|---------|-------|------------|-----------|")
    for b in pack.bottles:
        lines.append(_bottle_row(b))
    lines.append("")
    lines.append(
        "Deep-link — путь `/app/wine/<slug>` (mvp-plan.html, раздел 0: «страховка от плохого "
        "света на сцене» скана) — если камера/OCR не считывает этикетку живьём, открыть "
        "карточку напрямую по этому пути (закладка/адресная строка), не пересканировать "
        "бесконечно на глазах у гостя. Хост зависит от канала показа (демо-iPhone на "
        "staging, веб-версия с лендинга, localhost на прогоне) — генератору неизвестен, "
        "путь — универсальная часть."
    )
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
    lines.append(f"- Deep-link на случай плохого света (не пересканировать): `{s1['deep_link_fallback']}`")
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
        lines.append(
            f"- Достижимый инвариант: у винодельни «{s3['winery_name']}» "
            f"{_ru_wine_count(s3['winery_style_wine_count'])} этого стиля в каталоге — "
            f"живая выдача вправе назвать любое из них"
        )
        lines.append(f"- Иллюстративный пример из пака: **{s3['illustrative_wine_name']}** (`{s3['illustrative_wine_id']}`)")
        lines.append(f"- Критерий успеха: {s3['success_criteria']}")
    else:
        lines.append(f"- Недоступна: {s3['reason']}")

    rp = pack.refusal_probe
    lines.append("")
    lines.append("### Момент доверия — refusal_probe")
    lines.append("")
    if rp:
        lines.append(f"- Вопрос (из голд-сета калибровки, НЕ придуман): «{rp['question']}»")
        lines.append(f"- Источник: {rp['source']}")
        lines.append(f"- Критерий успеха: {rp['success_criteria']}")
        refusal_status = refusal_probe_status_text(report)
        lines.append(f"- Живая проверка: {refusal_status}")
    else:
        lines.append("- Недоступен: голд-сет калибровки не найден или пуст (см. предупреждения ниже).")

    lines.append("")
    lines.append("## Автопроверка")
    lines.append("")
    lines.append(f"- Обязательные поля непусты у всех {len(pack.bottles)} бутылок: {'OK' if not any(i.code == 'empty_required_field' for i in report.errors) else 'ОШИБКА'}")
    lines.append(f"- source_url отвечает 200 на вежливый HEAD: проверено {report.checked_urls} уникальных ссылок, предупреждений: {sum(1 for i in report.warnings if i.code in ('source_url_bad_status', 'source_url_unreachable'))}")
    lines.append(f"- Сцена 2 опирается на реальную гастропару пака: {'OK' if not any(i.code == 'scene2_pairing_not_grounded' for i in report.errors) else 'ОШИБКА'}")
    _scene3_error_codes = {"scene3_illustrative_wine_not_grounded", "scene3_style_not_grounded_in_winery"}
    lines.append(f"- Сцена 3 — стиль подтверждён в derived.reference_style_matches винодельни: {'OK' if not any(i.code in _scene3_error_codes for i in report.errors) else 'ОШИБКА'}")
    lines.append(f"- Deep-link на карточку есть у каждой из {len(pack.bottles)} бутылок: {'OK' if all(b.deep_link for b in pack.bottles) else 'ОШИБКА'}")
    if rp:
        lines.append(f"- refusal_probe реально отклоняется живым API: {refusal_status}")
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
    parser.add_argument("--goldset-path", type=Path, default=DEFAULT_GOLDSET_PATH, help="голд-сет калибровки RAG (type=refusal) для refusal_probe")
    parser.add_argument("--no-refusal-check", action="store_true", help="не проверять refusal_probe живым apps/api (офлайн/тесты)")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="адрес apps/api для живой проверки refusal_probe")
    parser.add_argument("--api-timeout", type=float, default=20.0, help="таймаут запроса к apps/api, сек")
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
            goldset_path=args.goldset_path,
        )
    except DemoPackError as exc:
        print(f"[demo_pack] ОШИБКА: {exc}", file=sys.stderr)
        return 2

    report = validate_pack(
        pack,
        check_network=not args.no_network,
        timeout=args.timeout,
        polite_delay=args.polite_delay,
        check_refusal_live=not args.no_refusal_check,
        api_url=args.api_url,
        api_timeout=args.api_timeout,
    )

    out_dir = args.out_dir / args.winery
    json_path, md_path = write_pack(pack, report, out_dir)

    print(f"[demo_pack] Винодельня: {pack.winery['name']} ({args.winery})")
    print(f"[demo_pack] Отобрано бутылок: {len(pack.bottles)} / запрошено {args.top_n} (в каталоге {pack.winery['wine_count_in_catalog']})")
    for scene_key in ("scene_1_scan", "scene_2_sommelier", "scene_3_analog"):
        scene = pack.scenario[scene_key]
        status = "готова" if scene.get("available", True) else f"недоступна ({scene.get('reason')})"
        print(f"[demo_pack] Сцена {scene['scene']} «{scene.get('title', scene_key)}»: {status}")
    if pack.refusal_probe:
        print(f"[demo_pack] Момент доверия «{pack.refusal_probe['question']}»: {refusal_probe_status_text(report)}")
    else:
        print("[demo_pack] Момент доверия: недоступен (голд-сет калибровки не найден)")
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
