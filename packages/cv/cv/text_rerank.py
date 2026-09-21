"""cv/text_rerank.py — текстовое переранжирование top-K CV-кандидатов OCR-текстом
этикетки (agents/G5-accuracy.md, задача 2; contracts/image-scan.md НЕ трогается —
это добавка поверх ImageIndex.search(), не замена; встраивание в apps/api — решение
оркестратора/B по отчёту reports/g5-accuracy.md, не эта задача).

Зачем: честный baseline (qa/scan-eval-runs/case-20260918-honest) — raw top-1 64.6%,
top-5 83.1%, средний gap между top-1 и следующим "чужим" кандидатом ~0.028 (reports/
f3-synthetic-baseline.md) — верный ответ ЧАСТО в top-K, но не первый: разрыв на
РАНЖИРОВАНИИ, не на поиске. cv/verify.py уже использует OCR для различения near-dup
СЕМЕЙ (один дизайн этикетки, разный год/категория) — здесь тот же источник сигнала
(мелкий текст этикетки: название/винодельня/сорт), но для ЛЮБОГО top-K, не только
near-dup — когда CV чуть ошибся, текст может подвинуть верный слаг на первое место.

Итоговый скор кандидата (только среди top-K, K — параметр вызова `rerank_top_k`):
    final = cv_score + w * text_score
где `w` и `K` подбираются на dev-сплите (qa/accuracy_lab.py), не здесь — этот модуль
знает только про сам text_score и механику переранжирования top-K.

## text_score: IDF-пересечение + rapidfuzz

text_score(ocr_text, candidate, idf) сочетает:
  1. IDF-взвешенное пересечение токенов запроса и текста кандидата (название +
     винодельня + сорт [+ регион] из каталога кейса, `load_catalog_text()`): редкие
     токены (винодельни, собственные названия) весят много, частые ("вино", "красное",
     "белое", "сухое", "брют", "россии" — почти в каждом документе каталога) — почти
     ничего. IDF выражает это сама, без ручного стоп-листа.
  2. rapidfuzz `token_set_ratio` — страхует от опечаток/шума OCR (пропущенная буква,
     слитные слова, EXIF-мусор), которые рвут точное токенное совпадение.

Вес нечёткой компоненты (`_FUZZY_WEIGHT`) — внутренняя константа модуля, НЕ часть
K/w-свипа брифа (тот подбирает только итоговую формулу cv+w*text); подобрана на
dev-сплите вместе с K/w (см. reports/g5-accuracy.md), не выведена аналитически.

## Транслитерация "с обеих сторон"

Один канонический алфавит — ЛАТИНИЦА: кириллица транслитерируется в неё, латиница
остаётся как есть. Применяется ОДИНАКОВО к обеим сторонам сравнения (тексту каталога
И OCR-тексту этикетки) — вот что значит "с обеих сторон" в задаче: не важно, с какой
стороны конкретно кириллица (этикетка «ARISTOV» -> "aristov", каталог «Аристов» ->
"aristov" — совпадение), а с какой латиница (бывает и наоборот — латинское название в
каталоге, кириллическая OCR-транскрипция на этикетке). Без этого шага rapidfuzz
сравнивал бы "аристов" с "aristov" посимвольно — огромное редакционное расстояние при
полном фонетическом совпадении, а точное токенное пересечение вообще ноль.
"""
from __future__ import annotations

import csv
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

from cv import config

# --------------------------------------------------------------------------------------
# Транслитерация + нормализация текста (см. докстринг модуля, "с обеих сторон")
# --------------------------------------------------------------------------------------

# Практическая схема кириллица->латиница — та же, что де-факто использована при
# генерации slug'ов каталога кейса (проверено на примерах: "мускатель"->"muskatel",
# "белый"->"belyy" в реальных slug'ах strapi_output0709.csv) — не научная транслитерация
# (ГОСТ/ISO), а та же бытовая схема, что видит сравнение, поэтому токены OCR/каталога и
# токены, УЖЕ сидящие в slug'ах, попадают в одно пространство практически без потерь.
_CYR_TO_LAT: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# После lower(): пропускаем всё, кроме латиницы/кириллицы/цифр/пробела — пунктуация,
# знаки объёма (0,75Л) и т.п. схлопываются в разделитель, не в мусорный токен.
_PUNCT_RE = re.compile(r"[^a-zа-яё0-9\s]")
_WS_RE = re.compile(r"\s+")


def translit_cyr_to_lat(text: str) -> str:
    """Посимвольная транслитерация кириллицы в латиницу (см. `_CYR_TO_LAT`); символы
    вне таблицы (латиница, цифры, пробелы — вход уже lower() к этому моменту)
    проходят как есть."""
    return "".join(_CYR_TO_LAT.get(ch, ch) for ch in text)


def normalize_text(text: str) -> str:
    """Канонический вид для сравнения: нижний регистр -> ё->е -> пунктуация в пробел ->
    кириллица->латиница (см. докстринг модуля, "с обеих сторон") -> схлопнутые пробелы.
    Пустая/пробельная строка -> "" (вызывающий код трактует как "нет сигнала")."""
    if not text:
        return ""
    lowered = text.lower().replace("ё", "е")
    stripped = _PUNCT_RE.sub(" ", lowered)
    translit = translit_cyr_to_lat(stripped)
    return _WS_RE.sub(" ", translit).strip()


def tokenize(text: str) -> list[str]:
    norm = normalize_text(text)
    return norm.split(" ") if norm else []


# --------------------------------------------------------------------------------------
# Каталог кейса: текст кандидата = название + винодельня + сорт (+ регион)
# --------------------------------------------------------------------------------------

_CSV_NAME_COL = "Название вина"
_CSV_WINERY_COL = "Винодельня"
_CSV_GRAPE_COL = "Сорт винограда"
_CSV_REGION_COL = "Регион"
_CSV_SLUG_COL = "Slug"

DEFAULT_CATALOG_CSV_NAME = "strapi_output0709.csv"


@dataclass(frozen=True)
class CatalogText:
    slug: str
    name: str = ""
    winery: str = ""
    grape: str = ""
    region: str = ""


def candidate_text(entry: CatalogText) -> str:
    """Сырой (ещё не нормализованный) текст кандидата — брифом заданный набор полей."""
    return " ".join(part for part in (entry.name, entry.winery, entry.grape, entry.region) if part)


def default_catalog_csv_path() -> Path:
    """`CV_CASE_CATALOG_CSV`, иначе `$CASE_DATA_DIR/strapi_output0709.csv` — тот же
    паттерн живого резолва env, что `cv.families.default_families_path()` (не
    замораживается при импорте, см. её докстринг)."""
    raw = os.environ.get("CV_CASE_CATALOG_CSV")
    if raw:
        return Path(raw)
    return config.CASE_DATA_DIR / DEFAULT_CATALOG_CSV_NAME


def load_catalog_text(csv_path: Path) -> dict[str, CatalogText]:
    """`slug -> CatalogText` из CSV каталога кейса (колонки «Название вина»,
    «Винодельня», «Сорт винограда», «Регион», «Slug»).

    Slug в CSV ДУБЛИРУЕТСЯ (несколько строк-фото на одну позицию, brief G5 п.2) —
    берём ПЕРВОЕ НЕПУСТОЕ значение на КАЖДОЕ поле независимо (не первую строку целиком):
    если первая строка слага несёт пустую "Сорт винограда", а третья — непустую, поле
    заполняется из третьей, не остаётся пустым только потому что первая строка была
    неполной. Пустой/отсутствующий slug -> строка пропущена (нет ключа для словаря)."""
    out: dict[str, CatalogText] = {}
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            slug = (row.get(_CSV_SLUG_COL) or "").strip()
            if not slug:
                continue
            name = (row.get(_CSV_NAME_COL) or "").strip()
            winery = (row.get(_CSV_WINERY_COL) or "").strip()
            grape = (row.get(_CSV_GRAPE_COL) or "").strip()
            region = (row.get(_CSV_REGION_COL) or "").strip()
            existing = out.get(slug)
            if existing is None:
                out[slug] = CatalogText(slug=slug, name=name, winery=winery, grape=grape, region=region)
            else:
                out[slug] = CatalogText(
                    slug=slug,
                    name=existing.name or name,
                    winery=existing.winery or winery,
                    grape=existing.grape or grape,
                    region=existing.region or region,
                )
    return out


# --------------------------------------------------------------------------------------
# IDF по каталогу
# --------------------------------------------------------------------------------------


def build_idf(catalog: dict[str, CatalogText]) -> dict[str, float]:
    """Сглаженный IDF (как sklearn `smooth_idf`): `log((N+1)/(df+1)) + 1` — всегда > 0
    (в т.ч. для токена, встреченного в КАЖДОМ документе — "вино"/"россии"-подобные
    почти ничего не весят, но не обнуляются совсем, что было бы хрупко при малых
    каталогах/тестовых фикстурах). N и df — по документам `candidate_text()` каждого
    слага каталога (не по всей публичной лексике русского языка)."""
    n_docs = len(catalog)
    df: dict[str, int] = {}
    for entry in catalog.values():
        tokens = set(tokenize(candidate_text(entry)))
        for t in tokens:
            df[t] = df.get(t, 0) + 1
    return {t: math.log((n_docs + 1) / (c + 1)) + 1.0 for t, c in df.items()}


# --------------------------------------------------------------------------------------
# text_score + переранжирование top-K
# --------------------------------------------------------------------------------------

# Внутренний баланс "точное IDF-пересечение" vs "нечёткость rapidfuzz" — НЕ часть
# K/w-свипа брифа (см. докстринг модуля). Подобран на dev вместе с K/w (reports/
# g5-accuracy.md) — здесь фиксированная константа, не аргумент, чтобы формула
# text_score() была детерминированной и юнит-тестируемой без скрытого состояния.
FUZZY_WEIGHT = 0.35


def distinctive_idf_threshold(idf: dict[str, float]) -> float:
    """Медиана словаря IDF каталога — граница "различающий токен" vs "стоп-слово-
    подобный" (винодельни/названия — у верхней половины шкалы; "вино"/"россии"-подобные
    токены — у нижней), без произвольной константы. Используется как дефолтный
    `min_token_idf` для `has_distinctive_token()`/`rerank_top_k(safe_gate=True)`."""
    values = sorted(idf.values())
    return values[len(values) // 2] if values else 0.0


def has_distinctive_token(ocr_text: str, idf: dict[str, float], min_token_idf: float) -> bool:
    """True, если хоть один токен OCR-текста — известное каталогу слово с IDF не ниже
    `min_token_idf`. Отсекает ДВЕ шумные категории разом: мусор OCR (которого в
    словаре каталога попросту нет — `idf.get(t, 0.0)` даёт 0) и общую лексику
    ("вино"/"красное"/"сухое" — есть в словаре, но с низким IDF, почти в каждом
    документе каталога). Обе — источник ложного текстового сигнала (находка G5,
    21.09: ~30% синтетических фото дают НЕПУСТОЙ, но бессодержательный OCR-текст —
    полный full-dev свип регрессировал уже на w=0.01 именно из-за них, пока text_score
    не был заперт этим гейтом — см. reports/g5-accuracy.md)."""
    if not ocr_text or not ocr_text.strip():
        return False
    return any(idf.get(t, 0.0) >= min_token_idf for t in tokenize(ocr_text))


def text_score(
    ocr_text: str,
    candidate: CatalogText | None,
    idf: dict[str, float],
    *,
    min_token_idf: float | None = None,
) -> float:
    """0.0, если OCR пуст (нет сигнала, брифовый тест "пустой OCR -> порядок CV") ИЛИ
    кандидат неизвестен каталогу (slug вне text-каталога — честная деградация, как и
    остальной код проекта: отсутствие данных не роняет вызывающий код) ИЛИ (когда задан
    `min_token_idf`) OCR-текст не содержит НИ ОДНОГО токена с IDF >= `min_token_idf`
    (`has_distinctive_token()`) — "безопасный гейт" (находка G5, 21.09; см. докстринг
    `has_distinctive_token`): без него даже мусорный/общий OCR-текст получает малый, но
    ненулевой rapidfuzz-скор против ЛЮБОГО кандидата и на большом объёме фото это шумом
    перевешивает выигрыш на фото, где OCR реально что-то полезное прочитал. `None`
    (по умолчанию) — гейт выключен, старое поведение 1:1 (обратная совместимость).

    Иначе: `idf-пересечение / idf(всех токенов кандидата)` (recall-по-кандидату,
    масштаб [0,1] — кандидат, все различающие токены которого нашлись в OCR, близко
    к 1; общие токены каталога почти не двигают числитель или знаменатель заметно) +
    `FUZZY_WEIGHT * rapidfuzz.token_set_ratio/100` (страховка от опечаток OCR)."""
    if candidate is None:
        return 0.0
    q_norm = normalize_text(ocr_text)
    if not q_norm:
        return 0.0
    if min_token_idf is not None and not has_distinctive_token(ocr_text, idf, min_token_idf):
        return 0.0
    c_norm = normalize_text(candidate_text(candidate))
    if not c_norm:
        return 0.0
    q_tokens = set(q_norm.split(" "))
    c_tokens = set(c_norm.split(" "))
    matched = q_tokens & c_tokens
    idf_total = sum(idf.get(t, 1.0) for t in c_tokens)
    idf_overlap = (sum(idf.get(t, 1.0) for t in matched) / idf_total) if idf_total > 0 else 0.0
    fuzzy = fuzz.token_set_ratio(q_norm, c_norm) / 100.0
    return idf_overlap + FUZZY_WEIGHT * fuzzy


def rerank_top_k(
    ranked: list[tuple[str, float]],
    ocr_text: str,
    catalog: dict[str, CatalogText],
    idf: dict[str, float],
    *,
    k: int = 10,
    w: float = 1.0,
    min_token_idf: float | None = None,
) -> list[tuple[str, float]]:
    """`ranked` — (slug, cv_score) уже отсортировано по убыванию cv_score (как
    `ImageIndex.search()` отдаёт `Match`, спроецированный на пару). Пересчитывает
    `cv_score + w*text_score` ТОЛЬКО для первых `k` элементов и пересортировывает их;
    хвост (k:) возвращается КАК ЕСТЬ, тем же порядком, со старым cv_score (контракт
    брифа: "Итог = cv_score + w·text_score НА TOP-K", не на всём списке).

    Пустой `ocr_text` -> `text_score` 0.0 для всех -> каждый элемент получает
    `cv_score + w*0 == cv_score` -> стабильная сортировка Python не меняет порядок
    относительно входного (уже отсортированного по cv_score) списка: итог совпадает
    с порядком CV один-в-один (брифовый тест-кейс).

    `min_token_idf` — безопасный гейт (см. `text_score`/`has_distinctive_token`):
    прокидывается в КАЖДЫЙ вызов `text_score` как есть, значит РОВНО ТО ЖЕ "пустой OCR
    -> порядок CV" происходит и для нераспознаваемого/общего OCR-текста, когда гейт
    включён — не только для буквально пустой строки. Рекомендованная конфигурация G5
    (reports/g5-accuracy.md) держит гейт ВКЛЮЧЁННЫМ (`text_rerank.distinctive_idf_
    threshold(idf)`) — без него шумный "непустой, но бессодержательный" OCR (~30%
    синтетики) в среднем вредил на full-сплите даже при малом w."""
    head = ranked[:k]
    tail = ranked[k:]
    scored = [
        (slug, cv_score + w * text_score(ocr_text, catalog.get(slug), idf, min_token_idf=min_token_idf))
        for slug, cv_score in head
    ]
    scored.sort(key=lambda kv: kv[1], reverse=True)
    return scored + tail
