"""OCR-верификатор near-dup семей — cv/verify.py (contracts/image-scan.md v0.4.4,
ревью 04, блокер 1: "реальный OCR-верификатор не существует и не имеет владельца —
а кейс проверяет именно его").

Зачем отдельно от ImageIndex: CV-эмбеддинг (SigLIP2) реагирует на общий облик этикетки
(цвет, композиция, иллюстрация) — у near-dup семьи (одна этикетка, разные год/категория/
объём) это визуально ОДНА И ТА ЖЕ картинка, ANN принципиально не может их развести
(cv/index.py — near-dup позиции нарочно попадают в одну "группу" по `gap`). Год/объём/
категория — мелкий печатный текст, который эмбеддинг не разрешает, а OCR — да.

Пайплайн `verify()`: decode (ValueError на битые байты) → `normalize_query()` (та же
нормализация, что видит `ImageIndex.search()` — этикетка уже кадрирована и выпрямлена,
OCR получает не сырое фото целиком, а стабилизированный регион) → PaddleOCR → токены
года/объёма/категории из распознанного текста → сопоставление с `VerifyCandidate.name`/
`vintage` → slug, если ровно один кандидат однозначно совпал, иначе `None`.

`None` — ЗАКОНОМЕРНЫЙ исход (контракт: "воздержался — API оставляет ANN-топ, честная
деградация, не ошибка"), не только на нечитаемом фото, но и когда текст неоднозначен
(два кандидата совпали одинаково хорошо) или кандидаты не несут различающего сигнала.
"""
from __future__ import annotations

import re
import time
from typing import TypedDict

import cv2
import numpy as np

from cv import imageio
from cv.normalize import normalize_query

# Год — ровно 4 цифры вида 19xx/20xx, не как часть более длинного числа (объём в мл
# вроде "1500" не должен читаться как год; "(?!\d)"/"(?<!\d)" — границы не-цифра).
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

# Объём — частые написания на российских этикетках: "0.75", "0,75", "750", "0.5", "500", "1.5", "1500".
_VOLUME_RE = re.compile(r"(?<!\d)(?:0[.,]75|750|0[.,]5|500|1[.,]5|1500)\s*(?:л|l|мл|ml)?\b", re.IGNORECASE)

# Категории сахара/типа — ключевые слова рус+лат, сведённые к канонической форме для
# сравнения. Не полный taxonomy.yaml (это справочник пайплайна данных, не моя зона) —
# минимальный набор, достаточный для tie-break внутри near-dup семьи по объявлению кейса.
_CATEGORY_KEYWORDS: dict[str, str] = {
    "брют": "брют",
    "brut": "брют",
    "экстра брют": "экстра брют",
    "extra brut": "экстра брют",
    "сухое": "сухое",
    "сухой": "сухое",
    "dry": "сухое",
    "sec": "сухое",
    "полусухое": "полусухое",
    "demi-sec": "полусухое",
    "полусладкое": "полусладкое",
    "сладкое": "сладкое",
    "sweet": "сладкое",
    "doux": "сладкое",
    "резерв": "резерв",
    "reserve": "резерв",
    "reserva": "резерв",
    "крепленое": "крепленое",
    "креплёное": "крепленое",
}

DEFAULT_SCORE_THRESH = 0.5  # ниже — OCR сам неуверен в строке, в токенизацию не пускаем
DEFAULT_LANG = "ru"  # PaddleOCR: кириллица + латиница (цифры/латинские слова — общий алфавит)
DEFAULT_OCR_SIZE = 320  # даунскейл перед OCR — доминирующий рычаг бюджета 700 мс, см. LabelVerifier.read_text


class VerifyCandidate(TypedDict):
    slug: str
    name: str  # из каталога: несёт год/категорию текстом
    vintage: int | None


def _extract_years(text: str) -> set[int]:
    return {int(m.group(0)) for m in _YEAR_RE.finditer(text)}


def _extract_volumes(text: str) -> set[str]:
    return {m.group(0).strip().rstrip("лlml мМлL").strip() for m in _VOLUME_RE.finditer(text)}


def _extract_categories(text_lower: str) -> set[str]:
    return {canon for kw, canon in _CATEGORY_KEYWORDS.items() if kw in text_lower}


def _tokens(text: str, name: str | None = None) -> tuple[set[int], set[str], set[str]]:
    """Токены года/объёма/категории из строки (запроса ИЛИ candidate.name — одна и та же
    логика с обеих сторон, чтобы сравнение было симметричным)."""
    combined = f"{text} {name}" if name else text
    return _extract_years(combined), _extract_volumes(combined), _extract_categories(combined.lower())


def match_candidates(ocr_text: str, candidates: list[VerifyCandidate]) -> str | None:
    """Чистая функция сопоставления (без OCR) — токены распознанного текста против
    `name`/`vintage` каждого кандидата. Вынесена отдельно от `LabelVerifier.verify()`,
    чтобы логику сопоставления можно было тестировать без реального движка OCR.

    Скоринг: год из vintage — самый надёжный сигнал (+2), год, распознанный ВНУТРИ
    name-строки (совпадающей с OCR) — тоже сильный (+2), категория — слабее (+1) сама
    по себе часто одинакова у всей near-dup семьи. Кандидат побеждает, только если его
    скор СТРОГО больше всех остальных (равенство или нулевой скор -> воздержание —
    честная неуверенность, а не потеря сигнала по невнимательности)."""
    if not candidates:
        return None

    ocr_years, ocr_volumes, ocr_categories = _tokens(ocr_text)
    if not ocr_years and not ocr_volumes and not ocr_categories:
        return None  # OCR ничего опознаваемого не нашёл — не из чего сопоставлять

    scored: list[tuple[float, str]] = []
    for c in candidates:
        name_years, name_volumes, name_categories = _tokens("", c.get("name"))
        vintage = c.get("vintage")

        score = 0.0
        if vintage is not None and vintage in ocr_years:
            score += 2.0
        elif name_years and (name_years & ocr_years):
            score += 2.0
        if name_volumes and (name_volumes & ocr_volumes):
            score += 1.0
        if name_categories and (name_categories & ocr_categories):
            score += 1.0

        if score > 0:
            scored.append((score, c["slug"]))

    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # неоднозначно — двое совпали одинаково хорошо
    return scored[0][1]


class LabelVerifier:
    """PaddleOCR (`lang=`, дефолт "ru" — кириллица; цифры и базовая латиница читаются
    тем же движком) поверх нормализованного региона этикетки запроса.

    Ленивая загрузка модели (как `cv.encoder.SiglipEncoder`) — конструктор и импорт
    модуля не должны тянуть веса/сеть, если верификатор в конкретном запуске не нужен.
    """

    def __init__(
        self,
        lang: str = DEFAULT_LANG,
        score_thresh: float = DEFAULT_SCORE_THRESH,
        ocr_size: int = DEFAULT_OCR_SIZE,
    ):
        self.lang = lang
        self.score_thresh = score_thresh
        self.ocr_size = ocr_size
        self._ocr = None

    def _load(self) -> None:
        if self._ocr is not None:
            return
        from paddleocr import PaddleOCR

        # Детект/развёртка документа и текстовой ориентации нам не нужны — normalize_query()
        # уже кадрировал и выпрямил этикетку; отключение этих стадий — заметная часть бюджета
        # 700 мс (см. reports/g-report.md, замеры).
        self._ocr = PaddleOCR(
            lang=self.lang,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    def read_text(self, image_arr: np.ndarray) -> str:
        """RGB ndarray -> распознанный текст (строки объединены пробелом), только
        куски с confidence >= `score_thresh`. Пустая строка, если OCR ничего не нашёл
        или сам движок упал (деградация, не исключение — см. `verify()`).

        Даунскейл до `ocr_size` ПЕРЕД детекцией — доминирующий рычаг бюджета 700 мс:
        детекция+распознавание PaddleOCR масштабируются с числом пикселей, а нужный
        текст (год/объём/категория) остаётся читаемым и на уменьшенном кадре — замер
        (см. reports/g-report.md): 448px ~800 мс, 320px ~420 мс, тот же текст, тот же
        результат сопоставления."""
        self._load()
        h, w = image_arr.shape[:2]
        if max(h, w) > self.ocr_size:
            scale = self.ocr_size / max(h, w)
            image_arr = cv2.resize(
                image_arr, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA
            )
        try:
            results = self._ocr.predict(image_arr)
        except Exception:  # noqa: BLE001 — сбой движка OCR = воздержание, не 500
            return ""
        if not results:
            return ""
        page = results[0]
        texts = page.get("rec_texts") if hasattr(page, "get") else getattr(page, "rec_texts", None)
        scores = page.get("rec_scores") if hasattr(page, "get") else getattr(page, "rec_scores", None)
        if not texts:
            return ""
        scores = scores or [1.0] * len(texts)
        kept = [t for t, s in zip(texts, scores) if s >= self.score_thresh]
        return " ".join(kept)

    def verify(self, image: bytes, candidates: list[VerifyCandidate]) -> str | None:
        """Контракт (image-scan.md v0.4.4). `image` — ЗАПРОС целиком (как в `ImageIndex.
        search()`), не предварительно вырезанный регион — `verify()` сам нормализует."""
        arr = imageio.decode_image(image)  # ValueError на битые/пустые байты — до OCR
        if not candidates:
            return None
        normalized = normalize_query(arr, enabled=True)
        text = self.read_text(normalized)
        return match_candidates(text, candidates)


def benchmark(verifier: LabelVerifier, images: list[bytes], candidates: list[VerifyCandidate], n: int | None = None) -> dict:
    """Тайминги `verify()` end-to-end (нормализация + OCR + сопоставление) — для
    отчёта (бюджет контракта: p95 <= 700 мс)."""
    if not images:
        return {"n": 0}
    n = n or len(images)
    imgs = [images[i % len(images)] for i in range(n)]

    timings = []
    for data in imgs:
        t0 = time.perf_counter()
        verifier.verify(data, candidates)
        timings.append((time.perf_counter() - t0) * 1000)

    arr = np.array(timings)
    return {
        "n": len(arr),
        "p50_ms": round(float(np.percentile(arr, 50)), 2),
        "p95_ms": round(float(np.percentile(arr, 95)), 2),
        "max_ms": round(float(arr.max()), 2),
        "mean_ms": round(float(arr.mean()), 2),
    }
