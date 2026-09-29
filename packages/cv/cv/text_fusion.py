"""cv/text_fusion.py — боевое слияние CV + текст этикетки по ВСЕМУ каталогу кейса
(agents/G7-text-fusion.md; ЗАМЕНА cv.text_rerank ТОЛЬКО когда включён CV_FUSION —
тот модуль не трогается и продолжает жить своей жизнью, см. его докстринг и
apps/api/app/cv/service.py про совместное включение обоих флагов).

## Основание (перенос прототипа, не изобретение заново)

Приватная проверка на реальных фото организаторов (21.09): стенд hack-v5 (CV +
верификатор) — top-1 ≈ 55%, top-5 ≈ 73%; `cv.text_rerank` (переранжирование top-K
готовых ANN-кандидатов) НИЧЕГО не меняет — верного вина часто нет в CV top-10
вовсе (Фанагория, Табия — see agents/G7-text-fusion.md), переранжировать нечего.
Прототип `qa/text_v2.py` + `qa/real_photos_eval.py` (схемы `v3_*`) на ТОЙ ЖЕ
выборке — top-1 ≈ 70-73%, top-5 ≈ 85-88%. Разница с `cv.text_rerank` НЕ в
арифметике text_score (тот тоже IDF-взвешенный), а в том, ЧТО и КАК сравнивается:

1. **Текст ищет по ВСЕМУ каталогу кейса** (`TextIndexV2` ниже, ~2100 слагов), не
   только среди top-K уже найденных CV кандидатов — верную винодельню/название
   текст находит, даже когда CV промахнулась мимо top-10 целиком.
2. **Гомоглифы OCR**: eslav-модель пишет кириллицу латинскими "двойниками" —
   CAMAPA=САМАРА, KPACHAA=КРАСНАЯ, ДЕHИCOB=ДЕНИСОВ (смешанный регистр
   кириллица+латиница), PO3E=РОЗЕ (кириллица с цифрой внутри) — `homoglyph_variant()`
   даёт токену кириллический вариант ДО общей транслитерации `cv.text_rerank`
   (та уже конвертирует кириллицу→латиницу, но не умеет узнать латинский
   omglyph как кириллицу — "CAMAPA" транслитерируется в "camapa", а не "samara").
   agents/H1-cpu-path.md (живые фото, 21.09): та же путаница бывает и с ГРЕЧЕСКИМИ
   заглавными — ровно для пяти кириллических букв, у которых нет латинского
   двойника, но есть графически неотличимый греческий (Λ→Л, Γ→Г, Π→П, Δ→Д, Φ→Ф;
   `_GREEK` ниже) — «OΛEΓ» читается OCR как О-Λ-Е-Γ (O/E — уже латинские двойники
   выше) и без греческой таблицы терял бы Λ/Γ целиком (не подстановка -> None,
   токен вообще не превращался бы в «ОЛЕГ»). Изолированный вклад на офлайн-прогоне
   (поверх центрального OCR-кропа): +3.2 п.п. top-1. agents/H2-rapidocr-multiscale.md
   (текст RapidOCR): ещё одна пара двойников — латинская строчная "i"→"и" (добавлена
   в `_LOW` ниже) — «Py6iH» читается OCR как P-y-6-i-H и без этой пары "i" не входит
   ни в один алфавит двойников, замена всего токена срывается; с ней — «Рубин». +1
   фото на офлайн-прогоне.
3. **Потокенная нечёткость** (`token_sim`, ratio >= 0.8 ИЛИ префикс >= 5 символов
   — "CKАЛИСТ"→"скалистый" по обрезке OCR) вместо одного `token_set_ratio` на
   всю строку кандидата — устойчивее к тому, что OCR обрывает ИМЕННО различающее
   слово, а не мусор вокруг него.
4. **Поля кандидата шире**: название + винодельня + сорт + категория (колонка
   `Категория` CSV кейса) + канонический маркер сахара (`sugar_of()`: слаг →
   "Название фото" → название) — категория и сахар различают SKU одной линейки
   (Жемчужная 9 сухое/полусухое/полусладкое — визуально одна картинка, разница
   только в подписи стиля).
5. **БЕЗ "безопасного гейта"** по медиане IDF (`cv.text_rerank.has_distinctive_token`)
   — на реальных фото этот гейт отсекал даже названия виноделен (слишком высокая
   планка для короткого OCR-текста этикетки). Сознательный откат от `cv.text_rerank`,
   не пробел: см. reports/g7-text-fusion.md.

## Итоговая формула (плато на разметке 100 живых фото, обе половины согласны)

`final = cv + W * rel`, где:
- `cv` — CV-скор кандидата, максимум по ДВУМ входам запроса: нормализованный кроп
  этикетки И весь кадр без нормализации (см. `ImageIndex.search_fusion()`, cv/index.py)
  — на реальных фото детектор этикетки (cv/normalize.py) часто берёт не то (блики,
  ракурс, полка), а весь кадр иногда несёт больше сигнала. Слагам БЕЗ эталона в
  индексе (usable=false и подобные — каталог кейса шире индекса) вместо CV-скора —
  заглушка `cv_top1 - CV_PAD` (иначе текст не может поднять то, чего CV не видит
  вовсе — конкурировать не с чем).
- `rel = mass / max(mass)` — IDF-масса совпавших токенов кандидата (`TextIndexV2.
  scores()`) относительно ЛУЧШЕГО кандидата ЭТОГО запроса (не абсолютная шкала —
  нормировка "на сколько я близок к идеальному текстовому совпадению этого фото").
- `W = 0.2` (`DEFAULT_W`).

Кандидаты слияния: CV top-50 слагов (`DEFAULT_ANN_TOP_K`) ∪ текст top-30 слагов по
`rel` (`DEFAULT_TEXT_TOP_N`) — не весь каталог целиком (бюджет), но заметно шире
top-5 ANN, которым ограничивался `cv.text_rerank`.

## Гейт «не подтверждена винодельня» (agents/H1-cpu-path.md, живые фото, 21.09)

Третья накопительная поправка CPU-пути (после центрального OCR-кропа и греческих
гомоглифов, offline 87.1% -> 88.7% top-1): текст в ПОЛСИЛЫ (`unconfirmed_winery_w`,
по умолчанию `×0.5` — параметр `CV_FUSION_OCR_UNCONFIRMED_W` в `app/cv/service.py`)
для кандидата, у которого ВИНОДЕЛЬНЯ не подтверждена запросом — recall токенов
ТОЛЬКО поля `winery` (отдельный `TextIndexV2(fields=("winery",))`, параметр
`winery_index` ниже) строго меньше `winery_recall_floor` (0.5). Мотив: короткий
зашумлённый OCR-текст живого фото иногда случайно набирает IDF-массу по названию/
сорту у ЧУЖОЙ винодельни (общие слова вида "резерв", "крю", цвет/сахар) — если сама
винодельня при этом текстом НЕ подтверждена, доверия к её текстовому сигналу меньше,
и `rel` этого кандидата урезается ДО умножения на `w` (инвариант `final = cv + w*rel`
сохраняется — в `rel` уже сидит урезанная величина, см. `fuse()`).

Гейт применяется ТОЛЬКО когда вызывающий код передал `winery_index` (иначе — старое
поведение 1:1, `unconfirmed_winery_w` по умолчанию 1.0 = "как сейчас" даже если
индекс передан). Источник текста важен: `app/cv/service.py` передаёt `×0.5` ТОЛЬКО
для `CV_FUSION_TEXT_SOURCE=ocr` — для VLM-источников (текст читает мультимодальная
модель, не OCR) тот же гейт на офлайн-прогоне ВРЕДЕН (95.2% -> 93.5% top-1: VLM
достаточно точна, чтобы урезание текста только теряло уже подтверждённый сигнал) —
там передаётся `1.0` (без эффекта).

### Алиасы написания винодельни (agents/ML-1-*.md, задача 2, 22.09)

Гейт выше наказывает и тогда, когда винодельня «не подтверждена» ЛОЖНО: каталог
записал ОДНОГО реального производителя ДВУМЯ разными строками поля «Винодельня»
(пример — слаг 93.97, «Поместье Голубицкое» на 2 SKU и «Golubitskoe Estate» на 28,
reports/ml-lead-plan.md), и recall по СВОЕЙ строке SKU не дотягивает до
`winery_recall_floor`, хотя запрос называет производителя ВЕРНО, просто другим
написанием, чем у конкретного SKU. `load_winery_index()` ниже читает проверенный
ВРУЧНУЮ список таких групп (`case-data/winery_aliases.json`,
`CV_WINERY_ALIASES_JSON`) и для слага из группы считает доктокены winery_index
ОБЪЕДИНЕНИЕМ токенов ВСЕХ строк группы — recall тогда отвечает на вопрос
"подтверждён ли производитель хоть одним из его известных написаний", а не
"подтверждена ли ИМЕННО строка каталога этого SKU". Файл отсутствует/пуст ->
прежнее поведение 1:1 (см. докстринг функции). Список НЕ строится автоматически по
частоте токенов поля winery — полный разбор (`qa/real_photos_winery_alias.py`)
показал, что одинаково редкие токены часто РОДОВЫЕ слова международного винного
брендинга (estate/vineyards/chateau/семейная — у РАЗНЫХ производителей), не имена
собственные; в файл идут только вручную подтверждённые пары.

## Гейт уверенности (новый, независимый от `CV_ABS_FLOOR`/`CV_MARGIN_FLOOR`)

Уверенно, если ОБА условия: (1) отрыв ИТОГОВОГО (`final`) скора top-1 от первого
кандидата ДРУГОЙ near-dup СЕМЬИ (`families.json`, та же перепись, что `cv/index.py`
использует для обычного `gap`) >= `CV_FUSION_GAP_FLOOR` (0.03) — ИЛИ такого
кандидата в рассмотренной вселенной вовсе нет (доминирование, та же трактовка
null-gap, что contracts/image-scan.md v0.4.7 §2 для обычного гейта — см. `fuse()`);
(2) CV-скор (НЕ итоговый, НЕ blended) top-1 >= `CV_FUSION_CV_FLOOR` (0.80). На
разметке: 96% точности уверенных ответов против ~60% у нынешнего гейта
(agents/G7-text-fusion.md, "Основание").
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz

from cv import config
from cv import text_rerank as tr

# --------------------------------------------------------------------------------------
# Гомоглифы OCR + токенизация запроса (перенос qa/text_v2.py, без изменений в арифметике)
# --------------------------------------------------------------------------------------

_UP = dict(zip("ABCEHKMOPTXY", "АВСЕНКМОРТХУ"))
# agents/H2-rapidocr-multiscale.md: "i" (латинская строчная) -> "и" — ВТОРАЯ пара
# двойников без кириллического аналога в исходном наборе, найдена на тексте RapidOCR
# («Py6iH» = «Рубин»: P->Р, y->у, 6->б, i->и, H->Н; без этой пары "i" не входит ни в
# один алфавит распознавания в homoglyph_variant() ниже, и вся замена срывается — та же
# дисциплина "все буквы токена — двойники", что и остальные пары, см. её докстринг).
_LOW = dict(zip("aceopxyui", "асеорхуии"))
_DIG = {"3": "З", "0": "О", "6": "б"}
# agents/H1-cpu-path.md: греческие заглавные двойники — ИМЕННО для пяти кириллических
# букв без латинского двойника (Б/Г/Д/Л/П/Ф/Ц/Ч/Ш/Щ и т.д. не совпадают с латиницей
# ни в одном начертании), но графически совпадающих с греческими: Λ→Л, Γ→Г, Π→П,
# Δ→Д, Φ→Ф (замер «OΛEΓ»→ОЛЕГ, докстринг модуля, п.2). Список НЕ расширяется на
# другие греческие буквы без отдельного измерения — так же, как _UP/_DIG не гадают
# латинские двойники без подтверждённого прецедента OCR.
# 22.09 (оркестратор, разбор промахов стенда hack-v6): пяти букв мало — RapidOCR пишет «ОΛΕΓ»
# с греческой Ε (и кириллической О в том же токене): токен смешанный кириллица+греческий,
# ни одна ветка homoglyph_variant() его не брала, оба фото «Табия — Олег» терялись. Таблица
# расширена на все заглавные греческие буквы, графически неотличимые от кириллицы (Α Β Ε Η Κ Μ
# Ο Ρ Τ Υ Χ), а перевод делается ДО разбора вариантов (`_greek_to_cyr` в query_tokens) — любая
# смесь алфавитов приводится к кириллице. Замер на 62 живых фото: 54 → 56 (CPU-путь, 2 кропа).
_GREEK = dict(zip("ΛΓΠΔΦΑΒΕΗΚΜΟΡΤΥΧ", "ЛГПДФАВЕНКМОРТУХ"))
_GREEK_TABLE = str.maketrans(_GREEK)
_CYR = re.compile(r"[а-яё]", re.I)
_LAT = re.compile(r"[a-z]", re.I)
_GRK = re.compile("[" + "".join(_GREEK) + "]")  # детекция «есть греческая буква» — не только известные 5
_YEAR = re.compile(r"^(19[5-9]\d|20[0-3]\d)$")


def homoglyph_variant(tok: str) -> str | None:
    """Кириллический вариант токена или None, если замена неприменима (см. докстринг
    модуля, п.2: CAMAPA/KPACHAA/ДЕHИCOB/PO3E-подобные написания OCR, плюс греческие
    заглавные двойники — OΛEΓ-подобные, `_GREEK`)."""
    has_cyr, has_lat, has_grk = bool(_CYR.search(tok)), bool(_LAT.search(tok)), bool(_GRK.search(tok))
    if has_lat and has_cyr:  # смешанный — латиница внутри кириллического слова
        return "".join(_UP.get(ch, _LOW.get(ch, ch)) for ch in tok)
    if (has_lat or has_grk) and not has_cyr:
        letters = [ch for ch in tok if ch.isalpha()]
        if letters and all(ch in _UP or ch in _GREEK for ch in letters):  # только «двойниковые» заглавные
            return "".join(_UP.get(ch, _GREEK.get(ch, _DIG.get(ch, ch))) for ch in tok)
        if letters and all(ch in _UP or ch in _LOW for ch in letters) and any(ch in _DIG for ch in tok):
            return "".join(_UP.get(ch, _LOW.get(ch, _DIG.get(ch, ch))) for ch in tok)
    if not has_lat and has_cyr and any(ch in _DIG for ch in tok):  # PO3E-подобные с цифрой внутри
        return "".join(_DIG.get(ch, ch) for ch in tok)
    return None


_EN_SUGAR = [
    (re.compile(r"\b(semi[\s-]?dry|demi[\s-]?sec|halbtrocken)\b", re.I), " polusuhoe "),
    (re.compile(r"\b(semi[\s-]?sweet|moelleux|halbs[uü]ss|lieblich)\b", re.I), " polusladkoe "),
    (re.compile(r"\bextra[\s-]?brut\b", re.I), " ekstra bryut "),
    (re.compile(r"\b(dry|sec|trocken|secco)\b", re.I), " suhoe "),
    (re.compile(r"\b(sweet|dolce|doux)\b", re.I), " sladkoe "),
    (re.compile(r"\bbrut\b", re.I), " bryut "),
]


def query_tokens(ocr_text: str) -> set[str]:
    """Токены OCR-текста запроса — каждый со своим гомоглиф-вариантом (если есть),
    оба прогнаны через `cv.text_rerank.tokenize()` (транслитерация "с обеих сторон").
    Английские маркеры сахара (dry/semi-sweet/brut/...) заменяются на русские ДО
    токенизации — тот же канонический словарь, что видит `sugar_of()` ниже."""
    out: set[str] = set()
    text = ocr_text or ""
    for rx, rep in _EN_SUGAR:
        text = rx.sub(rep, text)  # замена: «semi-dry» не должен дать ещё и «dry»→suhoe
    for raw in re.split(r"[\s·,;:/|()\"«»]+", text):
        raw = raw.strip(".-'’`")
        if not raw:
            continue
        variants = [raw]
        hv = homoglyph_variant(raw)
        if hv:
            variants.append(hv)
        greek = raw.translate(_GREEK_TABLE)  # «ОΛΕΓ» (кириллица + греческий) -> «ОЛЕГ»
        if greek != raw:
            variants.append(greek)
            hv2 = homoglyph_variant(greek)  # «OΛEΓ» с ЛАТИНСКИМИ O/E -> после перевода смешанный токен
            if hv2:
                variants.append(hv2)
        for v in variants:
            for t in tr.tokenize(v):
                if t.isdigit() and not _YEAR.match(t):
                    continue
                if len(t) >= 3 or _YEAR.match(t):
                    out.add(t)
    return out


def token_sim(q: str, c: str) -> float:
    """Потокенная нечёткость (докстринг модуля, п.3): точное совпадение -> 1.0;
    короткие/цифровые токены не сравниваются нечётко (шум); иначе rapidfuzz.ratio,
    если >= 0.8, либо бонус 0.85 за префикс (обрезка OCR — "CKАЛИСТ"→"скалистый"),
    когда оба токена длиной >= 5 и один — префикс другого."""
    if q == c:
        return 1.0
    if q.isdigit() or c.isdigit() or min(len(q), len(c)) < 4:
        return 0.0
    r = fuzz.ratio(q, c) / 100.0
    if r >= 0.8:
        return r
    if len(q) >= 5 and len(c) >= 5 and (c.startswith(q) or q.startswith(c)):
        return 0.85
    return 0.0


_SLUG_SUGAR = [("ekstra-bryut", "ekstra bryut"), ("polusuhoe", "polusuhoe"), ("polusladkoe", "polusladkoe"),
               ("bryut", "bryut"), ("desertn", "desertnoe")]
_PHOTO_SUGAR = [
    (re.compile(r"(экстра\s?брют|extra\s?brut)", re.I), "ekstra bryut"),
    (re.compile(r"(п\.\s?сух|п\s сух|полусух|semi[\s-]?dry|semidry)", re.I), "polusuhoe"),
    (re.compile(r"(п\.\s?сл|полусл|semi[\s-]?sweet)", re.I), "polusladkoe"),
    (re.compile(r"(брют|brut)", re.I), "bryut"),
    (re.compile(r"(сух|\bdry\b)", re.I), "suhoe"),
    (re.compile(r"(сладк|\bсл\.)", re.I), "sladkoe"),
]


def sugar_of(slug: str, photo_names: list[str], name: str) -> str:
    """Канонический маркер сахара позиции: из слага, иначе из имени фото/названия
    (докстринг модуля, п.4 — различает SKU одной линейки, например Жемчужная 9
    сухое/полусухое/полусладкое, у которых картинка эталона одна и та же)."""
    for key, val in _SLUG_SUGAR:
        if key in slug:
            return val
    if re.search(r"(^|-)suhoe", slug):
        return "suhoe"
    if re.search(r"(^|-)sladkoe", slug):
        return "sladkoe"
    for src in photo_names + [name]:
        for rx, val in _PHOTO_SUGAR:
            if rx.search(src or ""):
                return val
    return ""


class TextIndexV2:
    """Текстовый индекс кандидата: `slug -> набор токенов` по заданным полям +
    корпусный IDF (та же сглаженная формула, что `cv.text_rerank.build_idf`, но
    посчитанная на СВОЁМ словаре токенов ЭТОГО набора полей — категория/сахар
    добавляют токены, которых нет в словаре `cv.text_rerank`)."""

    def __init__(
        self,
        catalog: dict[str, "tr.CatalogText"],
        fields: tuple[str, ...] = ("name", "winery", "grape"),
        extra: dict[str, dict[str, str]] | None = None,
    ):
        """extra: slug -> {поле: текст} для полей вне CatalogText (category, sugar)."""
        self.catalog = catalog
        self.extra = extra or {}  # нужен `color_by_slug()` — цвет кандидата из поля category
        self.slugs = list(catalog)
        self.doc_tokens: list[set[str]] = []
        df: dict[str, int] = {}
        for s in self.slugs:
            e = catalog[s]
            toks: set[str] = set()
            for f in fields:
                val = getattr(e, f, None) if hasattr(e, f) else (extra or {}).get(s, {}).get(f, "")
                toks |= {t for t in tr.tokenize(val or "") if len(t) >= 3 or _YEAR.match(t)}
            self.doc_tokens.append(toks)
            for t in toks:
                df[t] = df.get(t, 0) + 1
        n = len(self.slugs)
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
        self.vocab = list(self.idf)

    def scores(self, ocr_text: str) -> tuple[list[float], list[float]]:
        """(recall по кандидату, абсолютная IDF-масса совпадений) для каждого слага,
        в порядке `self.slugs`. Пустой запрос (нет распознанных токенов) -> нули
        для всех, без обращения к rapidfuzz (дешёвый честный выход)."""
        q = query_tokens(ocr_text)
        if not q:
            return [0.0] * len(self.slugs), [0.0] * len(self.slugs)
        # лучший матч каждого слова словаря с запросом — один раз на словарь, не на
        # (слово словаря x документ) — экономит основную часть работы на плотном каталоге.
        best: dict[str, float] = {}
        for c in self.vocab:
            m = max(token_sim(t, c) for t in q)
            if m > 0:
                best[c] = m
        rec, mass = [], []
        for toks in self.doc_tokens:
            tot = sum(self.idf[t] for t in toks) or 1.0
            got = sum(self.idf[t] * best[t] for t in toks if t in best)
            rec.append(got / tot)
            mass.append(got)
        return rec, mass


# --------------------------------------------------------------------------------------
# Загрузка индекса из CSV каталога кейса (один раз, кэш по пути — см. docstring модуля)
# --------------------------------------------------------------------------------------

# Поля кандидата слияния (докстринг модуля, п.4): шире, чем cv.text_rerank (там
# только name/winery/grape[/region]) — категория и сахар различают SKU одной линейки.
FUSION_FIELDS: tuple[str, ...] = ("name", "winery", "grape", "category", "sugar")

_CSV_CATEGORY_COL = "Категория"
_CSV_PHOTO_NAME_COL = "Название фото"
_CSV_SLUG_COL = "Slug"  # то же значение, что cv.text_rerank._CSV_SLUG_COL (своя константа — не тянем чужой private)


def _load_extra_fields(csv_path: Path, catalog: dict[str, "tr.CatalogText"]) -> dict[str, dict[str, str]]:
    """slug -> {"category": ..., "sugar": ...} — поля вне `tr.CatalogText`, читаются
    отдельным проходом того же CSV (та же конвенция дублирующихся строк-фото на
    слаг, что `tr.load_catalog_text()`: `Название фото` копится списком на слаг,
    `Категория` — первое непустое значение)."""
    photo_names: dict[str, list[str]] = {}
    category: dict[str, str] = {}
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            slug = (row.get(_CSV_SLUG_COL) or "").strip()
            if not slug:
                continue
            photo_names.setdefault(slug, []).append(row.get(_CSV_PHOTO_NAME_COL) or "")
            if not category.get(slug):
                cat = (row.get(_CSV_CATEGORY_COL) or "").strip()
                if cat:
                    category[slug] = cat
    return {
        slug: {
            "category": category.get(slug, ""),
            "sugar": sugar_of(slug, photo_names.get(slug, []), catalog[slug].name if slug in catalog else ""),
        }
        for slug in catalog
    }


@lru_cache(maxsize=4)
def load_catalog_index(catalog_csv: str, fields: tuple[str, ...] = FUSION_FIELDS) -> TextIndexV2:
    """`TextIndexV2` по ВСЕМУ каталогу CSV кейса (`catalog_csv` — обычно
    `str(cv.text_rerank.default_catalog_csv_path())`, брифа: "как text_rerank.
    default_catalog_csv_path"). Строится ОДИН раз на процесс на каждый путь
    (`lru_cache`, тот же паттерн ключевания строкой, что `app/cv/service.py::
    _text_rerank_idf(case_data_dir: str)` — разные `CASE_DATA_DIR`/
    `CV_CASE_CATALOG_CSV` в разных тестах не видят чужой кэш).

    CSV кейса отсутствует на этой машине (свежий чекаут без `case-data/`, дев-
    профиль) -> пустой индекс (0 слагов) — `scores()` тогда честно отдаёт нули
    для всех запросов (нет катастрофы, слияние вырождается в чистый CV-ранкинг,
    см. `fuse()`), не исключение при старте процесса с `CV_FUSION=1`."""
    csv_path = Path(catalog_csv)
    if not catalog_csv or not csv_path.exists():
        return TextIndexV2({}, fields=fields, extra={})
    catalog = tr.load_catalog_text(csv_path)
    extra = _load_extra_fields(csv_path, catalog)
    return TextIndexV2(catalog, fields=fields, extra=extra)


# --------------------------------------------------------------------------------------
# Алиасы написания винодельни (agents/ML-1-*.md, задача 2) — ТОЛЬКО для winery_index,
# см. докстринг модуля "Алиасы написания винодельни"; основной индекс слияния
# (FUSION_FIELDS, load_catalog_index() выше) эта секция не строит и не трогает.
# --------------------------------------------------------------------------------------


def load_winery_alias_groups(path: Path) -> list[list[str]]:
    """Группы проверенных ВРУЧНУЮ написаний ОДНОЙ и той же винодельни
    (`case-data/winery_aliases.json`) — каждая группа: список строк поля
    «Винодельня» каталога РОВНО как они там записаны (не токены, не regex).

    Тот же принцип честной деградации, что `cv.families.load_family_by_slug()`:
    путь не существует, JSON битый, не словарь, нет ключа "groups" -> `[]`
    (вызывающий код, `load_winery_index()` ниже, тогда строит индекс 1:1, как до
    этой правки, ничего не ломая). Группа без хотя бы ДВУХ непустых строк
    отбрасывается — объединять нечего."""
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, list):
        return []
    out: list[list[str]] = []
    for g in raw_groups:
        strings = g.get("strings") if isinstance(g, dict) else None
        if not isinstance(strings, list):
            continue
        cleaned = [s.strip() for s in strings if isinstance(s, str) and s.strip()]
        if len(cleaned) >= 2:
            out.append(cleaned)
    return out


def default_winery_aliases_path() -> Path:
    """`CV_WINERY_ALIASES_JSON`, иначе `$CASE_DATA_DIR/winery_aliases.json` — тот
    же паттерн живого резолва env ПРИ КАЖДОМ ВЫЗОВЕ, что `cv.families.
    default_families_path()` (см. её докстринг: не замораживается при импорте
    модуля — вызывающий код читает актуальный env на момент вызова, не на
    момент импорта `cv.text_fusion`)."""
    raw = os.environ.get("CV_WINERY_ALIASES_JSON")
    if raw:
        return Path(raw)
    return config.CASE_DATA_DIR / "winery_aliases.json"


def load_winery_index(catalog_csv: str, aliases_json: "str | Path | None" = None) -> TextIndexV2:
    """`TextIndexV2` ТОЛЬКО по полю `winery` — то, что `_fusion_winery_index()` в
    `app/cv/service.py` строит СТРОГО с `fields=("winery",)` для гейта «не
    подтверждена винодельня» (`fuse(winery_index=...)`).

    Единственное отличие от голого `load_catalog_index(catalog_csv,
    fields=("winery",))`: слаг, чья СЫРАЯ строка «Винодельня» (после `strip()`)
    совпадает с ОДНОЙ из строк подтверждённой вручную группы алиасов
    (`load_winery_alias_groups()`), перед токенизацией получает поле winery,
    ЗАМЕНЁННОЕ на конкатенацию ВСЕХ строк этой группы через пробел —
    `TextIndexV2`/`tr.tokenize()` разбивают текст по пробелу на каждом шаге
    нормализации, поэтому конкатенация даёт РОВНО объединение множеств токенов
    отдельных строк (не приближение, не эвристика). Слаг вне любой группы
    получает свою строку как есть — 1:1, без изменений, регрессия исключена по
    построению.

    Честная деградация — аргумент `aliases_json` не передан и файл по умолчанию
    отсутствует, JSON пуст/битый, или НИ ОДНА строка каталога не совпала ни с
    одной группой -> результат ПОБИТОВО РАВЕН
    `load_catalog_index(catalog_csv, fields=("winery",))` (тот же кэшированный
    объект `TextIndexV2`, не копия) — та же дисциплина честного "нет файла — нет
    эффекта", что `cv.families.load_family_by_slug`. Основной индекс слияния
    (`FUSION_FIELDS`, `_fusion_text_index()`/`load_catalog_index()` без
    переопределения `fields`) эта функция не строит и не трогает вовсе — алиасы
    применяются ИСКЛЮЧИТЕЛЬНО к winery-only индексу (agents/ML-1-*.md, задача 2:
    "алиас только в winery_index")."""
    plain = load_catalog_index(catalog_csv, fields=("winery",))
    groups = load_winery_alias_groups(Path(aliases_json) if aliases_json else default_winery_aliases_path())
    csv_path = Path(catalog_csv)
    if not groups or not catalog_csv or not csv_path.exists():
        return plain  # нет групп алиасов или нет CSV каталога — 1:1, как раньше

    merged_text_by_string = {s.strip().casefold(): " ".join(strings) for strings in groups for s in strings}
    catalog = tr.load_catalog_text(csv_path)
    patched: dict[str, "tr.CatalogText"] = {}
    for slug, entry in catalog.items():
        merged_text = merged_text_by_string.get(entry.winery.strip().casefold())
        if merged_text is not None and merged_text != entry.winery:
            patched[slug] = replace(entry, winery=merged_text)
    if not patched:
        return plain  # ни один слаг каталога не попал ни в одну группу алиасов

    return TextIndexV2({**catalog, **patched}, fields=("winery",))


def top_text_slugs(index: TextIndexV2, mass: list[float], limit: int) -> list[str]:
    """Слаги индекса, ранжированные по убыванию IDF-массы (`mass`, из `index.
    scores()`), top-`limit`, только с ПОЛОЖИТЕЛЬНОЙ массой (нулевая масса — вообще
    нет пересечения с запросом, не кандидат). Ранжирование по `mass` эквивалентно
    ранжированию по `rel = mass/max(mass)` (масштабирование положительной
    константой не меняет порядок) — здесь `mass`, чтобы не пересчитывать `rel`
    только ради топ-N отбора кандидатов (см. `fuse()` про сам `rel` в итоговой
    формуле)."""
    order = sorted(range(len(index.slugs)), key=lambda i: mass[i], reverse=True)
    return [index.slugs[i] for i in order[:limit] if mass[i] > 0]


def text_top_slugs_for_ocr(index: TextIndexV2, ocr_text: str, limit: int) -> list[str]:
    """Удобство для вызывающего кода (apps/api): текст top-N слугов ДЛЯ ЭТОГО
    OCR-текста, не заботясь о промежуточном `mass`-векторе (нужен только состав
    "дополнительных" кандидатов ДО того, как CV посчитает свои скоры — см.
    `ImageIndex.search_fusion(extra_slugs=...)`, cv/index.py)."""
    if not index.slugs:
        return []
    _, mass = index.scores(ocr_text)
    return top_text_slugs(index, mass, limit)


# --------------------------------------------------------------------------------------
# Слияние: final = cv + W*rel, гейт уверенности (см. докстринг модуля)
# --------------------------------------------------------------------------------------

DEFAULT_W = 0.2
DEFAULT_CV_PAD = 0.03
DEFAULT_GAP_FLOOR = 0.03
DEFAULT_CV_FLOOR = 0.80
DEFAULT_ANN_TOP_K = 50
DEFAULT_TEXT_TOP_N = 30
# agents/H1-cpu-path.md: гейт «не подтверждена винодельня» (см. докстринг модуля).
DEFAULT_UNCONFIRMED_WINERY_W = 1.0  # 1.0 = как сейчас (нет эффекта) — CV_FUSION_UNCONFIRMED_WINERY_W
DEFAULT_WINERY_RECALL_FLOOR = 0.5  # recall (не mass!) поля winery ниже этого -> «не подтверждена»


@dataclass(frozen=True)
class FusedCandidate:
    slug: str
    final_score: float  # cv_score + w*rel — то, что видит UI/eval как `matches[i].score`
    cv_score: float  # НЕ blended — тот же смысл, что confidence.top1_score контракта
    rel: float  # mass/max(mass) этого запроса (0.0, если текст вообще не задел кандидата),
    # УРЕЗАННЫЙ `unconfirmed_winery_w`, если винодельня кандидата не подтверждена (agents/
    # H1-cpu-path.md, см. докстринг модуля) — инвариант final_score == cv_score + w*rel
    # держится всегда, урезание сидит именно здесь, а не отдельным множителем снаружи.


@dataclass(frozen=True)
class FusionResult:
    ranked: list[FusedCandidate]  # desc по final_score, вся кандидатная вселенная (CV top-K ∪ текст top-N)
    gap: float | None  # отрыв top-1 от первого кандидата ДРУГОЙ near-dup семьи, по final_score
    confident: bool  # гейт: gap floor (или доминирование) И cv_score(top1) >= cv_floor


# Цвет вина, прочитанный с этикетки, против цвета кандидата (22.09, оркестратор): «Мускатель белый»
# уходил в «Мускатель розовый» — OCR читал «БЕЛЫЙ», но CV перевешивала. Если в тексте найдено слово
# РОВНО ОДНОГО цвета, кандидаты с другим ИЗВЕСТНЫМ цветом (колонка «Категория» CSV) штрафуются.
# Замер на 62 живых фото: OCR-путь 56 → 57, пути с VLM-текстом без изменений при штрафе 0.03–0.08.
_COLOR_WORDS = {
    "white": re.compile(r"\b(бел(ое|ый|ая)|white|blanc)\b", re.I),
    "red": re.compile(r"\b(красн(ое|ый|ая)|red|rouge)\b", re.I),
    "rose": re.compile(r"\b(розов(ое|ый|ая)|розе|rose|rosé)\b", re.I),
}
_CATEGORY_COLOR = {"белое": "white", "красное": "red", "розовое": "rose"}
DEFAULT_COLOR_PENALTY = 0.0  # 0 = выключено; apps/api передаёт CV_FUSION_COLOR_PENALTY (дефолт 0.05)


def text_color(text: str) -> str | None:
    """Цвет вина по тексту этикетки: "white" | "red" | "rose", если найден РОВНО один; иначе None
    (нет слов цвета или их несколько — «розовое» рядом с «Красная стрелка» и т.п. — не гадаем)."""
    found = {c for c, rx in _COLOR_WORDS.items() if rx.search(text or "")}
    return next(iter(found)) if len(found) == 1 else None


def color_by_slug(index: "TextIndexV2") -> dict[str, str]:
    """slug -> "white" | "red" | "rose" по полю category каталога (оранжевое/пусто — без цвета)."""
    out: dict[str, str] = {}
    for slug, fields in (getattr(index, "extra", None) or {}).items():
        c = _CATEGORY_COLOR.get((fields.get("category") or "").strip().lower())
        if c:
            out[slug] = c
    return out


def _family_gap(ranked: list[FusedCandidate], family_by_slug: dict[str, str]) -> float | None:
    """Тот же принцип, что `cv.index._gaps_to_next_family` (перепись `families.json`,
    "семья из одного себя" для незарегистрированного слага), но по `final_score`
    слияния и только для TOP-1 (единственное, что нужно гейту `fuse()`) — `ranked`
    уже отсортирован по убыванию `final_score` вызывающим кодом."""
    if not ranked:
        return None
    top_family = family_by_slug.get(ranked[0].slug)
    for c in ranked[1:]:
        same_family = top_family is not None and family_by_slug.get(c.slug) == top_family
        if not same_family:
            return ranked[0].final_score - c.final_score
    return None  # доминирование: в рассмотренной вселенной нет чужака (v0.4.7 §2 трактовка)


def fuse(
    cv_scores: dict[str, float],
    text_index: TextIndexV2,
    ocr_text: str,
    *,
    family_by_slug: dict[str, str] | None = None,
    w: float = DEFAULT_W,
    cv_pad: float = DEFAULT_CV_PAD,
    text_top_n: int = DEFAULT_TEXT_TOP_N,
    gap_floor: float = DEFAULT_GAP_FLOOR,
    cv_floor: float = DEFAULT_CV_FLOOR,
    winery_index: "TextIndexV2 | None" = None,
    unconfirmed_winery_w: float = DEFAULT_UNCONFIRMED_WINERY_W,
    winery_recall_floor: float = DEFAULT_WINERY_RECALL_FLOOR,
    colors: dict[str, str] | None = None,
    color_penalty: float = DEFAULT_COLOR_PENALTY,
) -> FusionResult:
    """Слияние CV + текст по всему каталогу (см. докстринг модуля).

    `cv_scores` — {slug: cv_score} КАНДИДАТОВ CV (обычно `ImageIndex.search_fusion()`,
    уже max(norm,raw) для CV top-K ∪ запрошенных текстовых extra_slugs, см. cv/index.py)
    — пустой словарь (CV не нашла вообще ничего, например индекс пуст) -> честный
    пустой результат, "CV обязателен, OCR — усилитель" (contracts/image-scan.md,
    первая строка): текст НИКОГДА не создаёт кандидатов сам по себе, когда CV
    отказала целиком.

    Кандидатная вселенная — объединение `cv_scores` (уже согласовано вызывающим
    кодом с `DEFAULT_ANN_TOP_K`) и текст top-`text_top_n` ПО ЭТОМУ же `ocr_text`
    (пересчитывается здесь заново из `text_index.scores()` — вызывающий код мог
    использовать ТЕ ЖЕ текстовые кандидаты, чтобы попросить их CV-скор у
    `search_fusion(extra_slugs=...)` ДО вызова `fuse()`, поэтому набор совпадёт).
    Слаг с текстовым сигналом, но БЕЗ эталона в индексе вообще (значит и без
    записи в `cv_scores`, даже после `extra_slugs`-фильтра) получает CV-заглушку
    `cv_top1 - cv_pad`, где `cv_top1 = max(cv_scores.values())` — иначе текст не
    может поднять то, чего CV в принципе не видит (докстринг модуля, п. "cv").

    `winery_index`/`unconfirmed_winery_w`/`winery_recall_floor` (agents/H1-cpu-path.md,
    см. докстринг модуля, "Гейт «не подтверждена винодельня»"): `winery_index` — ОТДЕЛЬНЫЙ
    `TextIndexV2`, построенный ТОЛЬКО на поле `winery` (не смешивается с основным
    `text_index`, у которого поля шире, см. `FUSION_FIELDS`) — `None` (дефолт) отключает
    гейт целиком, бит-в-бит поведение до этой правки. Когда передан: кандидат, чей
    RECALL (не mass — доля, не абсолютная величина) по `winery_index.scores(ocr_text)`
    строго меньше `winery_recall_floor`, получает `rel`, умноженный на
    `unconfirmed_winery_w` — ДО того, как `rel` идёт в `final_score = cv + w*rel`
    (значит и до нормировки `top_mass` следующего кандидата эта нормировка не трогается:
    `top_mass` считается по ОСНОВНОМУ, не по урезанному `rel`, чтобы шкала "на сколько я
    близок к лучшему текстовому совпадению" оставалась общей для всех кандидатов)."""
    if not cv_scores:
        return FusionResult(ranked=[], gap=None, confident=False)

    _, mass = text_index.scores(ocr_text)
    mass_by_slug = dict(zip(text_index.slugs, mass)) if text_index.slugs else {}
    top_mass = max(mass) if mass else 0.0
    text_top = top_text_slugs(text_index, mass, text_top_n) if text_index.slugs else []

    winery_recall_by_slug: dict[str, float] = {}
    if winery_index is not None and winery_index.slugs:
        winery_rec, _ = winery_index.scores(ocr_text)
        winery_recall_by_slug = dict(zip(winery_index.slugs, winery_rec))

    label_color = text_color(ocr_text) if (color_penalty and colors) else None

    universe = dict.fromkeys(cv_scores)
    for slug in text_top:
        universe.setdefault(slug, None)

    cv_top1 = max(cv_scores.values())
    candidates: list[FusedCandidate] = []
    for slug in universe:
        cv = cv_scores.get(slug)
        if cv is None:
            cv = cv_top1 - cv_pad  # заглушка: слаг вне индекса, но текст его различил
        rel = (mass_by_slug.get(slug, 0.0) / top_mass) if top_mass > 0 else 0.0
        if winery_recall_by_slug and winery_recall_by_slug.get(slug, 0.0) < winery_recall_floor:
            rel *= unconfirmed_winery_w  # винодельня НЕ подтверждена текстом — текст в unconfirmed_winery_w силы
        final = cv + w * rel
        if label_color and colors and colors.get(slug, label_color) != label_color:
            final -= color_penalty  # цвет кандидата противоречит слову цвета на этикетке
        candidates.append(FusedCandidate(slug=slug, final_score=final, cv_score=cv, rel=rel))

    candidates.sort(key=lambda c: c.final_score, reverse=True)
    gap = _family_gap(candidates, family_by_slug or {})
    confident = candidates[0].cv_score >= cv_floor and (gap is None or gap >= gap_floor)
    return FusionResult(ranked=candidates, gap=gap, confident=confident)
