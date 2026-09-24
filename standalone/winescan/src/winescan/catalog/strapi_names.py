"""Имена файлов Strapi Upload и их сопоставление с исходными именами из CSV.

Strapi сохраняет загруженный файл как ``<slugify(name, separator="_")>_<10 hex>.<ext>``,
а в поле ``name`` (именно его выгрузили в CSV как «Название фото») остаётся исходное имя.
slugify (@sindresorhus/slugify) транслитерирует кириллицу, разбивает CamelCase
(``DSC09173`` -> ``DSC_09173``, ``fpZEJh`` -> ``fp_ZE_Jh``) и заменяет прочие символы на ``_``.
Точно повторять slugify не нужно: сравниваем по ключу «транслит, только [a-z0-9]».

Кроме оригиналов Strapi кладёт рядом уменьшенные копии ``thumbnail_``/``small_``/
``medium_``/``large_`` + имя оригинала; для поиска они не нужны.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import PurePosixPath

FORMAT_PREFIXES = ("thumbnail_", "small_", "medium_", "large_")

IMAGE_EXTENSIONS = frozenset(
    {".webp", ".png", ".jpg", ".jpeg", ".jfif", ".heic", ".tif", ".tiff", ".gif", ".avif", ".bmp"}
)

_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh",
    "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "cz",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "є": "ye", "і": "i", "ї": "yi", "ґ": "g",
    # slugify выбрасывает «№», а NFKD превратил бы его в «No»
    "№": "",
}  # fmt: skip

_HASH_SUFFIX = re.compile(r"^(?P<base>.*)_(?P<hash>[0-9a-f]{10})$")
_NON_KEY_CHARS = re.compile(r"[^0-9a-z]")


def transliterate(text: str) -> str:
    """Кириллица -> латиница (как в slugify Strapi), диакритика снимается."""
    latin = "".join(_CYRILLIC.get(ch.lower(), ch) for ch in text)
    decomposed = unicodedata.normalize("NFKD", latin)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def split_extension(name: str) -> tuple[str, str]:
    """Отделяет расширение изображения; неизвестный «суффикс» (``бел.сух``) остаётся в имени."""
    name = name.strip()
    suffix = PurePosixPath(name).suffix
    if suffix.lower() in IMAGE_EXTENSIONS:
        return name[: -len(suffix)], suffix.lower()
    return name, ""


def name_key(stem: str) -> str:
    """Ключ сравнения имени без расширения: транслит, нижний регистр, только [a-z0-9]."""
    return _NON_KEY_CHARS.sub("", transliterate(stem).lower())


def photo_key(photo_name: str) -> str:
    """Ключ для исходного имени фото из CSV (с расширением)."""
    return name_key(split_extension(photo_name)[0])


def separator_key(stem: str) -> str:
    """Ключ с границами слов: различает «aligote-rkatsiteli» и «aligoterkatsiteli».

    Для имён, где slugify разбил CamelCase (``DSC_09173``), с CSV не совпадёт, поэтому
    годится только для выбора среди кандидатов с одинаковым :func:`name_key`.
    """
    return re.sub(r"[^0-9a-z]+", "_", transliterate(stem).lower()).strip("_")


@dataclass(frozen=True)
class UploadName:
    filename: str
    base: str
    file_hash: str | None
    ext: str
    key: str


def parse_upload(filename: str) -> UploadName:
    stem, ext = split_extension(filename)
    match = _HASH_SUFFIX.match(stem)
    base, file_hash = (match["base"], match["hash"]) if match else (stem, None)
    return UploadName(filename=filename, base=base, file_hash=file_hash, ext=ext, key=name_key(base))


def is_format_variant(filename: str, all_filenames: Collection[str]) -> bool:
    """Уменьшенная копия Strapi: ``<prefix>_<имя существующего оригинала>``.

    Проверяем наличие оригинала, а не только префикс, чтобы не выкинуть файл,
    который пользователь сам назвал, например, ``small_bottle.webp``.
    """
    return any(
        filename.startswith(prefix) and filename[len(prefix) :] in all_filenames
        for prefix in FORMAT_PREFIXES
    )
