#!/usr/bin/env python3
"""qa/case_census.py — перепись датасета кейса РСХБ (agents/F3-census.md, qa/acceptance.md §9):
slug -> эталонное фото матчер v2 (транслитерация кириллицы) + перепись near-dup семей +
шумовая перепись uploads + качество эталонов.

Чистая логика (транслитерация/нормализация/матчинг/семьи/шум) — без PIL/cv2, тестируется
под обычным `qa/.venv` (qa/tests/test_case_census.py). Единственное место, которому НУЖНЫ
пиксели (`make_pil_size_lookup` — выбор лучшего файла среди >1 кандидата и перепись
разрешений, задачи 1/5 брифа) импортирует PIL ЛЕНИВО внутри функции — модуль в целом
импортируется без PIL. У qa/.venv PIL нет; вместо установки лишней зависимости в свою зону
реальный прогон использует уже готовый packages/cv/.venv (там PIL/cv2 давно стоят для
других задач G) — см. докстринг `--write`-прогона ниже. Это НЕ переустановка PaddleOCR,
просто переиспользование уже установленного Pillow из чужого (read-only) venv.

Транслитерация кириллицы выверена ЭМПИРИЧЕСКИ по реальным парам «Название фото» (CSV) ↔
имя файла в uploads/ (Strapi транслитерирует кириллические имена при аплоаде) — не по
исходникам npm-пакета `transliteration` (сеть недоступна/не нужна, пакета нет локально).
Пары-свидетели для каждой буквы — reports/f3-case-census.md, раздел «Транслитерация».
Ё/Щ ни разу не встретились среди промахов — их отображение (yo/shch) не подтверждено парой,
это явно помечено в отчёте.

Запуск (полный, с записью в case-data/slug_refs.json + case-data/families.json):

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\
    /Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye/packages/cv/.venv/bin/python \\
        /Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye/qa/case_census.py --write

(HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE здесь не нужны этому конкретному скрипту — выставлены
для единообразия с остальными командами дня датасета, см. §9 acceptance.md.)

Без --write — только считает и печатает сводку + пишет qa/case-census-run/stats.json
(своя зона, не case-data/), ничего в case-data/ не трогает.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

_QA_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _QA_DIR.parent

DEFAULT_CASE_DATA_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
UPLOADS_SUBPATH = Path("prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads")
CSV_NAME = "strapi_output0709.csv"

PREVIEW_PREFIXES = ("thumbnail_", "small_", "medium_", "large_")
PHOTO_EXTS = {"webp", "jpg", "jpeg", "png"}

# --------------------------------------------------------------------------------------
# Транслитерация — см. докстринг модуля и reports/f3-case-census.md для пар-свидетелей.
# --------------------------------------------------------------------------------------

CYRILLIC_TO_LATIN: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "cz", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
# Буквы БЕЗ пары-свидетеля в реальных данных этого датасета (см. докстринг модуля).
UNCONFIRMED_LETTERS = frozenset({"ё", "щ"})

_HASH_SUFFIX_RE = re.compile(r"(?:_[0-9a-f]{10})+$")
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
_YEAR_IN_SLUG_RE = re.compile(r"^(?:19|20)\d{2}$")


def has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC_RE.search(text))


def transliterate(text: str) -> str:
    """Кириллица -> латиница посимвольно (см. CYRILLIC_TO_LATIN). Регистр не сохраняется
    посимвольно — вход приводится к lower() перед отображением, т.к. итоговое сравнение
    (normalize_key) всё равно регистронезависимо; сохранять оригинальный Title Case Strapi
    (виден в реальных файлах, напр. "Spumante_belyj_bryut") не нужно для матчинга по ключу."""
    return "".join(CYRILLIC_TO_LATIN.get(ch, ch) for ch in text.lower())


def strip_hash_suffix(stem: str) -> str:
    """Срезает ОДИН ИЛИ НЕСКОЛЬКО хвостовых `_<10 hex>` — Strapi добавляет новый хэш при
    каждой повторной загрузке уже захэшированного имени; часть файлов несёт цепочку из
    2-3 таких суффиксов (напр. `..._704fdf2136_4e49dedcd5_428751153f.webp`)."""
    return _HASH_SUFFIX_RE.sub("", stem)


def normalize_key(text: str) -> str:
    """Ключ сравнения: транслитерация -> не-алфанум в «_» -> схлопнуть -> обрезать края."""
    t = transliterate(text)
    t = _NON_ALNUM_RE.sub("_", t)
    return t.strip("_")


def split_stem_ext(filename: str) -> tuple[str, str]:
    p = Path(filename)
    return p.stem, p.suffix.lower().lstrip(".")


def is_preview_filename(name: str) -> bool:
    return name.startswith(PREVIEW_PREFIXES)


# --------------------------------------------------------------------------------------
# CSV -> slug_table
# --------------------------------------------------------------------------------------


def load_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build_slug_table(rows: Iterable[dict[str, str]]) -> dict[str, dict[str, str]]:
    """slug -> {name, winery, photo, category, grape}. Дубли строк на один slug в CSV
    несут ОДНО И ТО ЖЕ имя фото (проверено на полном датасете: 0 слагов с >1 различным
    «Название фото» из 2103 — см. reports/f3-case-census.md) — берём последнюю встреченную
    строку, без потери информации."""
    table: dict[str, dict[str, str]] = {}
    for r in rows:
        slug = (r.get("Slug") or "").strip()
        if not slug:
            continue
        table[slug] = {
            "name": (r.get("Название вина") or "").strip(),
            "winery": (r.get("Винодельня") or "").strip(),
            "photo": (r.get("Название фото") or "").strip(),
            "category": (r.get("Категория") or "").strip(),
            "grape": (r.get("Сорт винограда") or "").strip(),
        }
    return table


def parse_grape_set(grape_field: str) -> frozenset[str]:
    """'Менье, Пино Нуар, Шардоне' -> {'менье','пино нуар','шардоне'} — сравнение
    Cyrillic-в-Cyrillic, транслитерация не нужна (не сопоставляется с латиницей)."""
    if not grape_field:
        return frozenset()
    return frozenset(p.strip().lower() for p in grape_field.split(",") if p.strip())


# --------------------------------------------------------------------------------------
# uploads/ -> индекс ключ -> файлы
# --------------------------------------------------------------------------------------


def build_upload_index(filenames: Iterable[str]) -> dict[str, list[str]]:
    """Превью (thumbnail_/small_/medium_/large_) отбрасываются — они не эталоны.
    Не-фото расширения (svg/pdf/xml/geojson/tif/heic/jfif — см. census_noise) ТОЖЕ
    отбрасываются здесь: эталон вина не может быть логотипом/PDF/картой по случайному
    совпадению нормализованного имени — такие файлы идут только в шумовую перепись
    (задача 4 брифа), никогда не выбираются матчером."""
    idx: dict[str, list[str]] = defaultdict(list)
    for fn in filenames:
        if is_preview_filename(fn):
            continue
        stem, ext = split_stem_ext(fn)
        if ext not in PHOTO_EXTS:
            continue
        key = normalize_key(strip_hash_suffix(stem))
        if key:
            idx[key].append(fn)
    return dict(idx)


def find_exact_candidates(photo_name: str, index: dict[str, list[str]]) -> list[str]:
    stem, _ext = split_stem_ext(photo_name)
    key = normalize_key(stem)
    return list(index.get(key, [])) if key else []


_SUBSTRING_MIN_LEN = 6
_SUBSTRING_MIN_LEN_RATIO = 0.5


def find_substring_candidates(photo_name: str, index: dict[str, list[str]]) -> list[str]:
    """Резерв для промахов точного ключа: ключ фото — подстрока ключа файла ИЛИ наоборот.
    Порог длины 6 САМ ПО СЕБЕ оказался недостаточен — найден реальный ложный кейс:
    `preview_166ff5f67a.webp` (ключ "preview", 7 симв.) совпадал по подстроке сразу с 9
    ключами вида "..._no_bg_preview_carve_photos" (экспорты стороннего фон-вырезателя
    carve.photos — их значимая часть имени это случайный ID вида '4285_eqF1Fau', никак не
    связанный ни с одним файлом в uploads; "preview" там — случайный общий токен, не сигнал
    сходства). Фикс: короткая сторона должна быть НЕ МЕНЕЕ ПОЛОВИНЫ длины длинной — общий
    короткий токен внутри намного более длинной чужой строки больше не засчитывается.
    НЕ гадаем: вызывающая сторона принимает результат только если он ровно один файл —
    иначе это неоднозначность, остаётся промахом (см. classify_unmatched)."""
    stem, _ext = split_stem_ext(photo_name)
    key = normalize_key(stem)
    if len(key) < _SUBSTRING_MIN_LEN:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for k, files in index.items():
        if len(k) < _SUBSTRING_MIN_LEN:
            continue
        if not (key in k or k in key):
            continue
        shorter, longer = (k, key) if len(k) <= len(key) else (key, k)
        if len(shorter) / len(longer) < _SUBSTRING_MIN_LEN_RATIO:
            continue
        for fn in files:
            if fn not in seen:
                seen.add(fn)
                out.append(fn)
    return out


def classify_unmatched(photo_name: str, index: dict[str, list[str]]) -> str:
    """'no_ref' — ни один достаточно длинный (>=4 симв.) фрагмент нормализованного имени
    не встречается НИ В ОДНОМ ключе uploads -> высокая уверенность, что фото физически
    отсутствует в поставке (не забыли найти, а его правда нет — см. reports/f3-case-census.md,
    проверено вручную на DSC09173.webp/base64-именах: 0 вхождений даже без нормализации).
    'weak_lexical_overlap' — какой-то фрагмент где-то встречается, но не даёт уверенного
    кандидата — оставлено для ручного разбора, не тюнинг вслепую."""
    stem, _ext = split_stem_ext(photo_name)
    key = normalize_key(stem)
    words = [w for w in key.split("_") if len(w) >= 4]
    if not words:
        return "no_ref"
    for w in words:
        for k in index:
            if w in k:
                return "weak_lexical_overlap"
    return "no_ref"


def resolve_best_candidate(
    candidates: list[str], size_lookup: Callable[[str], tuple[int, int] | None]
) -> str:
    """'Лучший' = максимальное разрешение (площадь w*h); недоступный размер -> -1 (в конец).
    Тай-брейк — имя файла по алфавиту, для детерминизма."""
    if len(candidates) == 1:
        return candidates[0]

    def area(fn: str) -> int:
        wh = size_lookup(fn)
        return wh[0] * wh[1] if wh else -1

    return sorted(candidates, key=lambda fn: (-area(fn), fn))[0]


def load_manual_matches(path: Path) -> dict[str, str | list[str]]:
    """agents/F4-data-hygiene.md, задача 2: ручной словарь `slug -> filename` (uploads/)
    для случаев, где имя фото в CSV настолько отличается от загруженного файла, что ни
    точный ключ, ни фолбэк по подстроке не находят уверенного кандидата (см. докстринг
    qa/manual_photo_matches.yaml для метода разбора). Отсутствующий файл -> пустой
    словарь (тот же принцип, что `cv.cli.load_near_dup_groups` — не ошибка, а "словаря
    пока нет"). `yaml` — ленивый импорт (тот же приём, что PIL в `make_pil_size_lookup`
    выше): чистая логика модуля не должна требовать лишней зависимости на уровне файла.

    D1 (agents/D1-ref-collisions.md, задача 4): значение может быть СПИСКОМ, а не только
    строкой — `[chosen, *extra]` (подтверждённые вручную «тоже это вино» доп. ракурсы)
    или `[]` (явное решение «ни один кандидат не подходит» для слога с len(candidates)>1,
    см. run_matcher). Список пропускается как список (не превращается в строку) — см.
    ветвление в run_matcher, где отличие str/list меняет ПРАВИЛО применения оверрайда."""
    if not path.is_file():
        return {}
    import yaml

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: ожидался плоский словарь slug -> filename, получено {type(payload)}")
    out: dict[str, str | list[str]] = {}
    for k, v in payload.items():
        out[str(k)] = [str(x) for x in v] if isinstance(v, list) else str(v)
    return out


def run_matcher(
    slug_table: dict[str, dict[str, str]],
    upload_filenames: Iterable[str],
    size_lookup: Callable[[str], tuple[int, int] | None] = lambda fn: None,
    manual_matches: dict[str, str | list[str]] | None = None,
) -> dict[str, Any]:
    index = build_upload_index(upload_filenames)
    mapping: dict[str, Any] = {}
    no_ref_slugs: list[str] = []
    unmatched_by_name: list[dict[str, Any]] = []

    for slug, info in slug_table.items():
        photo = info["photo"]
        exact = find_exact_candidates(photo, index)
        entry: dict[str, Any] = {
            "name": info["name"],
            "winery": info["winery"],
            "photo_csv": photo,
            "candidates": [],
            "chosen": None,
            "match_method": None,
        }
        if exact:
            entry["candidates"] = exact
            entry["chosen"] = resolve_best_candidate(exact, size_lookup)
            entry["match_method"] = (
                "cyrillic_transliteration" if has_cyrillic(photo) else "normalized_stem"
            )
        else:
            sub = find_substring_candidates(photo, index)
            if len(sub) == 1:
                entry["candidates"] = sub
                entry["chosen"] = sub[0]
                entry["match_method"] = "substring_fallback"
            elif len(sub) > 1:
                entry["candidates"] = sub
                unmatched_by_name.append(
                    {"slug": slug, "photo_csv": photo, "reason": "ambiguous_multi_candidate", "candidates": sub}
                )
            else:
                cls = classify_unmatched(photo, index)
                if cls == "no_ref":
                    no_ref_slugs.append(slug)
                else:
                    unmatched_by_name.append(
                        {"slug": slug, "photo_csv": photo, "reason": cls, "candidates": []}
                    )
        mapping[slug] = entry

    # F4, задача 2: словарь (значение-строка) консультируется ТОЛЬКО для слогов, которых
    # автоматика выше НЕ сматчила (chosen is None) — никогда не может перекрыть уже
    # успешный автоматический матч, даже если словарь устарел/содержит опечатку. Ссылка
    # на файл, которого больше нет на диске (устаревшая запись словаря), тоже молча
    # пропускается — не ошибка сборки.
    #
    # D1 (agents/D1-ref-collisions.md, задача 4): значение-СПИСОК — это ручной разбор
    # коллизии `len(candidates) > 1` (Screenshot_N.webp и т.п. — РАЗНЫЕ винодельни
    # используют один и тот же нормализованный стем, resolve_best_candidate() выше мог
    # выбрать ЧУЖОЙ файл среди кандидатов). В отличие от строки, список ПРИМЕНЯЕТСЯ
    # ДАЖЕ КОГДА entry["chosen"] уже проставлен автоматикой — это и есть исправляемый
    # баг, не "второе мнение поверх успеха". `[chosen, *extra]` -> chosen становится
    # эталоном, extra уходит в НОВОЕ поле `extra_refs` (доп. ракурсы «тоже это вино»,
    # подтверждённые вручную — cv/cli.py::discover_refs_from_slug_refs_json индексирует
    # ТОЛЬКО их, не сырой `candidates`, см. докстринг там). `[]` — явное решение "ни
    # один кандидат не подходит" (agents/D1-ref-collisions.md п.3): chosen -> None,
    # candidates -> [] (слог честно возвращается в no_ref_slugs ниже), extra_refs не
    # пишем вовсе. Файлы, которых больше нет на диске, тихо отфильтровываются из списка
    # (тот же принцип, что для строки) — если после фильтрации список пуст, это
    # трактуется как reject, а не как "словарь сломан".
    known_files = {fn for files in index.values() for fn in files}
    applied_manual: set[str] = set()
    for slug, value in (manual_matches or {}).items():
        entry = mapping.get(slug)
        if entry is None:
            continue
        if isinstance(value, list):
            vetted = [fn for fn in value if fn in known_files]
            if vetted:
                entry["chosen"] = vetted[0]
                entry["candidates"] = vetted
                entry["extra_refs"] = vetted[1:]
                entry["match_method"] = "manual_override_reviewed"
            else:
                entry["chosen"] = None
                entry["candidates"] = []
                entry.pop("extra_refs", None)
                entry["match_method"] = "manual_override_rejected"
            applied_manual.add(slug)
        else:
            filename = value
            if entry["chosen"] is not None or filename not in known_files:
                continue
            entry["chosen"] = filename
            entry["candidates"] = [filename]
            entry["match_method"] = "manual_override"
            applied_manual.add(slug)
    if applied_manual:
        rejected = {s for s in applied_manual if mapping[s]["chosen"] is None}
        no_ref_slugs = [s for s in no_ref_slugs if s not in applied_manual]
        no_ref_slugs = sorted(set(no_ref_slugs) | rejected)
        unmatched_by_name = [e for e in unmatched_by_name if e["slug"] not in applied_manual]

    multi_candidate_slugs = sorted(s for s, e in mapping.items() if len(e["candidates"]) > 1)

    shared_files: dict[str, list[str]] = defaultdict(list)
    for slug, e in mapping.items():
        if e["chosen"]:
            shared_files[e["chosen"]].append(slug)
    shared_families = {fn: sorted(slugs) for fn, slugs in shared_files.items() if len(slugs) > 1}

    return {
        "mapping": mapping,
        "no_ref_slugs": sorted(no_ref_slugs),
        "unmatched_by_name": sorted(unmatched_by_name, key=lambda d: d["slug"]),
        "multi_candidate_slugs": multi_candidate_slugs,
        "shared_files": shared_families,
        "manual_override_count": len(applied_manual),
    }


# --------------------------------------------------------------------------------------
# Near-dup семьи: (а) год-в-slug, (б) общий файл, (в) похожее «Название вина»
# --------------------------------------------------------------------------------------


def slug_year_and_base(slug: str) -> tuple[str, str | None]:
    """(база-без-года, год|None). Год — ПЕРВЫЙ дефис-разделённый токен вида 19xx/20xx
    (год может стоять не только в конце: 'leto-kaberne-fran-rezerv-2020-suhoe-krasnoe')."""
    parts = slug.split("-")
    year: str | None = None
    kept: list[str] = []
    for p in parts:
        if year is None and _YEAR_IN_SLUG_RE.match(p):
            year = p
            continue
        kept.append(p)
    return "-".join(kept), year


def normalize_wine_name(name: str) -> str:
    t = name.lower()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


# Категории/сахар — ТОТ ЖЕ словарь, что packages/cv/cv/verify.py::_CATEGORY_KEYWORDS
# (скопировано, не импортировано: этот модуль обязан импортироваться БЕЗ cv2/numpy под
# обычным qa/.venv — см. докстринг модуля; cv.verify тянет их на уровне модуля). Список
# статичный, риск рассинхронизации с G низкий; если изменится — синхронизировать руками.
_CATEGORY_WORDS = frozenset({
    "брют", "brut", "экстра", "extra", "сухое", "сухой", "dry", "sec",
    "полусухое", "demi", "sec", "полусладкое", "сладкое", "sweet", "doux",
    "резерв", "reserve", "reserva", "крепленое", "креплёное",
})
_YEAR_TOKEN_IN_TEXT_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def name_similarity_key(name: str) -> str:
    """«Скелет» названия для строгого сравнения near-dup: без явного года и без
    категорийных/сахарных слов (_CATEGORY_WORDS) — именно то, что по case.md МОЖЕТ
    отличаться у near-dup серии ("одна серия, разные год/сезон/категория ПРИ ОДИНАКОВОЙ
    ЭТИКЕТКЕ"). Сорт винограда/цвет НЕ вычищается умышленно — это разные этикетки, не
    near-dup (см. reports/f3-case-census.md, кейс 'alma-valley-solntse-vozduh-...',
    отловленный на прежней версии с процентным порогом расстояния — ложная склейка
    разных сортов одной продуктовой линейки)."""
    t = normalize_wine_name(name)
    t = _YEAR_TOKEN_IN_TEXT_RE.sub(" ", t)
    words = [w for w in t.split() if w not in _CATEGORY_WORDS]
    return " ".join(words).strip()


def _grapes_conflict(g1: frozenset[str], g2: frozenset[str]) -> bool:
    """True, если у ОБОИХ указан сорт винограда И они НИ РАЗУ не пересекаются — сильный
    сигнал, что это разные этикетки одной продуктовой линейки, не near-dup. Найдено на
    реальных данных: 'Blush #1' (Сира) / 'Blush #2' (Мерло) / 'Blush #3' (Каберне Фран) /
    'Blush #4' (Пино Нуар) — названия различаются на 1 цифру (проходили бы и скелет-, и
    дистанционную проверку), но это четыре РАЗНЫХ сорта/этикетки. case.md определяет
    near-dup как ОДНУ И ТУ ЖЕ этикетку с разным годом/сезоном/категорией — не разный
    сорт. Смешанные купажи (напр. 'Рислинг, Ркацители, Шардоне' vs 'Алиготе, Рислинг,
    Шардоне') пересекаются -> НЕ конфликт, остаются кандидатом в семью."""
    return bool(g1) and bool(g2) and g1.isdisjoint(g2)


def find_name_similarity_pairs(
    slug_table: dict[str, dict[str, str]], max_abs_distance: int = 2
) -> list[tuple[str, str]]:
    """Пары слагов ОДНОЙ винодельни, чьё «Название вина» near-dup по смыслу case.md.
    Скоуп «одна винодельня» — не только производительность (иначе O(n^2) по всем 2103),
    но и смысл: near-dup — всегда одна линейка одной винодельни, не совпадение названий
    у разных виноделен.

    Пара засчитывается, если ЛИБО (а) «скелет» названия (без года/категории) совпадает
    буквально — прямое попадание в определение case.md, ЛИБО (б) расстояние Левенштейна
    между ПОЛНЫМИ нормализованными названиями <= порога — опечатки/форматирование (лишний
    пробел, дефис), НЕ замена целого слова.

    Порог (б) — АБСОЛЮТНЫЙ (не процент от длины: первая версия, 15% от длины, на реальных
    данных ложно объединяла разные сорта одной продуктовой линейки на длинных названиях —
    чем длиннее строка, тем больше символов разрешал процентный порог), НО дополнительно
    ЗАЖАТ длиной короткой стороны: на очень коротких «названиях» абсолютный потолок в 2
    правки — это фактически ЛЮБАЯ пара (найдено на реальных данных: винодельня Belmas
    называет вина 2-буквенными кодами сорта — 'Cf'/'Pg'/'Pn'/'Vi' — расстояние Левенштейна
    между ЛЮБЫМИ двумя различными 2-буквенными кодами уже <= 2, что ложно объединяло вина
    РАЗНЫХ сортов; см. reports/f3-case-census.md)."""
    by_winery: dict[str, list[str]] = defaultdict(list)
    for slug, info in slug_table.items():
        by_winery[info["winery"]].append(slug)

    pairs: list[tuple[str, str]] = []
    for slugs in by_winery.values():
        if len(slugs) < 2:
            continue
        names = {s: normalize_wine_name(slug_table[s]["name"]) for s in slugs}
        skeletons = {s: name_similarity_key(slug_table[s]["name"]) for s in slugs}
        grapes = {s: parse_grape_set(slug_table[s].get("grape", "")) for s in slugs}
        for i in range(len(slugs)):
            for j in range(i + 1, len(slugs)):
                s1, s2 = slugs[i], slugs[j]
                n1, n2 = names[s1], names[s2]
                if not n1 or not n2:
                    continue
                if _grapes_conflict(grapes[s1], grapes[s2]):
                    continue
                k1, k2 = skeletons[s1], skeletons[s2]
                if k1 and k1 == k2:
                    pairs.append((s1, s2))
                    continue
                if n1 == n2:
                    pairs.append((s1, s2))
                    continue
                threshold = min(max_abs_distance, max(1, min(len(n1), len(n2)) // 3))
                if levenshtein(n1, n2) <= threshold:
                    pairs.append((s1, s2))
    return pairs


class _UnionFind:
    def __init__(self, items: Iterable[str]):
        self.parent = {x: x for x in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def classify_differentiator(
    members: list[str],
    slug_table: dict[str, dict[str, str]],
    chosen_file_by_slug: dict[str, str | None],
    slug_year: dict[str, str | None],
) -> str:
    """Приоритет (см. agents/F3-census.md, задача 2): общий файл важнее года/категории —
    если референсы физически совпадают, семья неразличима CV независимо от метаданных."""
    member_files = [chosen_file_by_slug.get(m) for m in members]
    if all(f is not None for f in member_files) and len(set(member_files)) == 1:
        return "одинаковый-файл-неразличимы"

    years = [slug_year.get(m) for m in members]
    if all(y is not None for y in years) and len(set(years)) > 1:
        return "год-в-slug"
    if any(y is None for y in years) and any(y is not None for y in years):
        return "год-в-этикетке-отсутствует"

    cats = {slug_table[m]["category"] for m in members if slug_table[m]["category"]}
    if len(cats) > 1:
        return "категория"
    return "прочее"


def build_families(
    slug_table: dict[str, dict[str, str]], chosen_file_by_slug: dict[str, str | None]
) -> dict[str, Any]:
    all_slugs = list(slug_table.keys())
    uf = _UnionFind(all_slugs)

    # (а) год-в-slug
    year_groups: dict[str, list[str]] = defaultdict(list)
    slug_year: dict[str, str | None] = {}
    for slug in all_slugs:
        base, year = slug_year_and_base(slug)
        slug_year[slug] = year
        year_groups[base].append(slug)
    for members in year_groups.values():
        if len(members) > 1:
            for m in members[1:]:
                uf.union(members[0], m)

    # (б) общий эталон-файл
    file_groups: dict[str, list[str]] = defaultdict(list)
    for slug, fn in chosen_file_by_slug.items():
        if fn:
            file_groups[fn].append(slug)
    for members in file_groups.values():
        if len(members) > 1:
            for m in members[1:]:
                uf.union(members[0], m)

    # (в) похожее «Название вина» той же винодельни
    for a, b in find_name_similarity_pairs(slug_table):
        uf.union(a, b)

    components: dict[str, list[str]] = defaultdict(list)
    for slug in all_slugs:
        components[uf.find(slug)].append(slug)

    families: dict[str, Any] = {}
    for members in components.values():
        if len(members) < 2:
            continue
        members = sorted(members)
        family_id = members[0]
        families[family_id] = {
            "slugs": members,
            "differentiator": classify_differentiator(members, slug_table, chosen_file_by_slug, slug_year),
            "chosen_files": {m: chosen_file_by_slug.get(m) for m in members},
        }
    return families


# --------------------------------------------------------------------------------------
# Шумовая перепись uploads (оригиналы, не являющиеся ничьим эталоном)
# --------------------------------------------------------------------------------------

_SPECIAL_EXT_BUCKETS = {
    "svg": "svg_logo",
    "geojson": "geojson_map",
    "xml": "xml_sitemap",
    "pdf": "pdf_doc",
    "tif": "tif_scan",
    "tiff": "tif_scan",
    "heic": "heic_photo",
    "jfif": "jfif_photo",
}
_CAMERA_NAME_RE = re.compile(r"^(?:dsc|img|pxl|mg)[_-]?\d", re.IGNORECASE)


def classify_noise_file(filename: str) -> str:
    """Эвристика по расширению/имени — приблизительная, не претендует на 100% точность
    (см. reports/f3-case-census.md, оговорка); достаточна для оценки порядка величин."""
    stem, ext = split_stem_ext(filename)
    bucket = _SPECIAL_EXT_BUCKETS.get(ext)
    if bucket:
        return bucket
    if ext not in PHOTO_EXTS:
        return f"other_ext_{ext or 'none'}"
    base = strip_hash_suffix(stem)
    if _CAMERA_NAME_RE.match(base):
        return "camera_original"
    has_sep = "_" in base or "-" in base or " " in base
    class_count = sum([any(c.isupper() for c in base), any(c.islower() for c in base), any(c.isdigit() for c in base)])
    if not has_sep and len(base) >= 16 and class_count >= 2:
        return "random_s3_name"
    return "other_photo"


def census_noise(upload_filenames: list[str], reference_files: set[str]) -> dict[str, Any]:
    originals = [fn for fn in upload_filenames if not is_preview_filename(fn)]
    noise_files = [fn for fn in originals if fn not in reference_files]
    counts: dict[str, int] = defaultdict(int)
    examples: dict[str, list[str]] = defaultdict(list)
    for fn in noise_files:
        bucket = classify_noise_file(fn)
        counts[bucket] += 1
        if len(examples[bucket]) < 5:
            examples[bucket].append(fn)
    return {
        "total_uploads": len(upload_filenames),
        "total_originals": len(originals),
        "total_previews": len(upload_filenames) - len(originals),
        "total_references": len(reference_files),
        "total_noise": len(noise_files),
        "by_bucket": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "examples_by_bucket": dict(examples),
    }


# --------------------------------------------------------------------------------------
# Качество эталонов — распределение разрешений
# --------------------------------------------------------------------------------------


def _percentile(sorted_vals: list[int], p: float) -> int | None:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, round(p / 100 * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def census_reference_quality(
    chosen_file_by_slug: dict[str, str | None],
    size_lookup: Callable[[str], tuple[int, int] | None],
    small_threshold: int = 400,
) -> dict[str, Any]:
    shorts: list[int] = []
    small: list[dict[str, Any]] = []
    unreadable: list[dict[str, str]] = []
    for slug, fn in sorted(chosen_file_by_slug.items()):
        if not fn:
            continue
        wh = size_lookup(fn)
        if wh is None:
            unreadable.append({"slug": slug, "file": fn})
            continue
        w, h = wh
        short = min(w, h)
        shorts.append(short)
        if short < small_threshold:
            small.append({"slug": slug, "file": fn, "w": w, "h": h})
    shorts.sort()
    return {
        "n": len(shorts),
        "min_short_side": shorts[0] if shorts else None,
        "p50_short_side": _percentile(shorts, 50),
        "p95_short_side": _percentile(shorts, 95),
        "max_short_side": shorts[-1] if shorts else None,
        "small_threshold": small_threshold,
        "small_count": len(small),
        "small_examples": small[:30],
        "unreadable_count": len(unreadable),
        "unreadable_examples": unreadable[:10],
    }


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def make_pil_size_lookup(uploads_dir: Path) -> Callable[[str], tuple[int, int] | None]:
    """PIL — ЛЕНИВЫЙ импорт (только здесь), qa/.venv его не ставит (см. докстринг модуля):
    реальный прогон использует packages/cv/.venv, где Pillow уже стоит для нужд G, ничего
    не переустанавливаем. `Image.open()` без `.load()` читает только заголовок — быстро
    даже на ~2100 файлах."""
    from PIL import Image

    cache: dict[str, tuple[int, int] | None] = {}

    def _lookup(fn: str) -> tuple[int, int] | None:
        if fn in cache:
            return cache[fn]
        path = uploads_dir / fn
        try:
            with Image.open(path) as im:
                size = (im.width, im.height)
        except Exception:  # noqa: BLE001 — битый/нечитаемый файл -> просто "нет размера"
            size = None
        cache[fn] = size
        return size

    return _lookup


def write_slug_refs(path: Path, result: dict[str, Any]) -> None:
    payload = {
        "mapping": result["mapping"],
        "no_ref_slugs": result["no_ref_slugs"],
        "unmatched_by_name": result["unmatched_by_name"],
        "multi_candidate_slugs": result["multi_candidate_slugs"],
        "shared_files": result["shared_files"],
        "manual_override_count": result.get("manual_override_count", 0),
        "generated_by": "qa/case_census.py",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_families(path: Path, families: dict[str, Any]) -> None:
    path.write_text(json.dumps(families, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case-data-dir", type=Path, default=DEFAULT_CASE_DATA_DIR)
    parser.add_argument("--write", action="store_true", help="перезаписать slug_refs.json/families.json в case-data-dir")
    parser.add_argument(
        "--stats-out", type=Path, default=_QA_DIR / "case-census-run" / "stats.json",
        help="куда сохранить сводный JSON статистики (своя зона qa/, не case-data/)",
    )
    parser.add_argument(
        "--manual-matches", type=Path, default=_QA_DIR / "manual_photo_matches.yaml",
        help="ручной словарь slug -> filename для слогов вне автоматического матчера "
        "(F4, agents/F4-data-hygiene.md, задача 2) — отсутствующий файл -> пустой словарь",
    )
    args = parser.parse_args(argv)

    case_dir: Path = args.case_data_dir
    csv_path = case_dir / CSV_NAME
    uploads_dir = case_dir / UPLOADS_SUBPATH

    t0 = time.perf_counter()
    rows = load_csv_rows(csv_path)
    slug_table = build_slug_table(rows)
    upload_filenames = sorted(p.name for p in uploads_dir.iterdir() if p.is_file())

    size_lookup = make_pil_size_lookup(uploads_dir)
    manual_matches = load_manual_matches(args.manual_matches)

    result = run_matcher(slug_table, upload_filenames, size_lookup=size_lookup, manual_matches=manual_matches)
    chosen_file_by_slug = {slug: e["chosen"] for slug, e in result["mapping"].items()}
    families = build_families(slug_table, chosen_file_by_slug)

    reference_files = {fn for fn in chosen_file_by_slug.values() if fn}
    noise = census_noise(upload_filenames, reference_files)
    quality = census_reference_quality(chosen_file_by_slug, size_lookup)

    matched = sum(1 for e in result["mapping"].values() if e["chosen"])
    summary = {
        "slugs_total": len(slug_table),
        "slugs_matched": matched,
        "slugs_matched_manual_override": result.get("manual_override_count", 0),
        "slugs_no_ref": len(result["no_ref_slugs"]),
        "slugs_unmatched_by_name": len(result["unmatched_by_name"]),
        "multi_candidate_slugs": len(result["multi_candidate_slugs"]),
        "shared_file_groups": len(result["shared_files"]),
        "families_total": len(families),
        "families_slugs_total": sum(len(f["slugs"]) for f in families.values()),
        "noise_total": noise["total_noise"],
        "quality_p50_short_side": quality["p50_short_side"],
        "quality_p95_short_side": quality["p95_short_side"],
        "quality_small_count": quality["small_count"],
        "elapsed_s": round(time.perf_counter() - t0, 1),
    }

    stats = {
        "summary": summary,
        "match_result": result,
        "families": families,
        "noise": noise,
        "quality": quality,
    }

    if args.write:
        write_slug_refs(case_dir / "slug_refs.json", result)
        write_families(case_dir / "families.json", families)

    args.stats_out.parent.mkdir(parents=True, exist_ok=True)
    args.stats_out.write_text(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
