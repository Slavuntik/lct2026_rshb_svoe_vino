"""Наш сканер за контрактом `ImageIndex` монорепо (`apps/api/app/cv/interface.py`).

Зачем. В монорепо уже есть движок распознавания (`packages/cv`: SigLIP 2 base, Qdrant,
25 ракурсов на позицию, OCR-верификатор). Этот адаптер добавляет **второй** движок, не
трогая оркестрацию: `apps/api/app/cv/service.py::run_photo_scan` работает со списком
`Match(slug, score, gap, view)` и ничего больше от движка не требует, поэтому выбор делается
переменной `IMAGE_PROVIDER`, а умолчание — общим замером (`docs/scan-engines.md`).

Что наш движок добавляет к `packages/cv`: детектор бутылки OWLv2 с обучаемым выбором рамки
(в кадре с несколькими бутылками берётся нужная), SigLIP 2 so400m в двух видах (упаковка и
этикетка), мультиракурсная галерея, проверка кандидатов по локальным признакам (SIFT +
гомография) и рамка, указанная пользователем.

Честные оговорки на стыке контрактов:

* `gap` в монорепо — отрыв top-1 от первого кандидата **не из его near-dup семьи**
  (`packages/cv/cv/index.py::_gaps_to_next_family`). Наш сканер семей не знает и по умолчанию
  отдаёт отрыв от следующего кандидата выдачи: это консервативнее — внутри одной серии отрыв
  мал, поэтому «не найдено» срабатывает чаще, чем у родного движка. Если рядом лежит перепись
  семей кейса (`CV_FAMILIES_JSON`), адаптер считает `gap` по их правилу, и величины становятся
  сопоставимы напрямую.
* `view` контракт описывает как ``"real" | "synth-N"``. Наша галерея хранит несколько поворотов
  на вино и наружу не сообщает, какой сматчился, поэтому возвращается ``"real"``: поле не влияет
  на решение, только на отчётность.
* `embed()` намеренно не реализован. У нас два индекса (упаковка и этикетка) и одного «вектора
  кадра» не существует — вернуть один список чисел значило бы соврать о том, как устроен поиск.
* `build`/`add` тоже не реализованы: галерея собирается отдельной командой
  (`python -m winescan.search.build_index --yaws=-30,-15,0,15,30`), адаптер — только на чтение.
"""

from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from PIL import Image

log = logging.getLogger("winescan.integration")

# «кадр целиком» в долях: этим наш сканер выражает отказ от детектора — тот же путь,
# которым приходит рамка, указанная пользователем в интерфейсе
WHOLE_FRAME = (0.0, 0.0, 1.0, 1.0)


@dataclass
class Match:
    """Зеркало `apps/api/app/cv/interface.py::Match` (структурная совместимость)."""

    slug: str
    score: float
    gap: float | None
    view: str


class WinescanImageIndex:
    """Провайдер `IMAGE_PROVIDER=winescan`.

    Конструктор без обязательных аргументов — того же образца, что `cv.index.ImageIndex`:
    настройки читаются из окружения (`WINESCAN_*`). Модели грузятся лениво, при первом
    обращении; прогревает их фабрика монорепо (`warm_up_image_index`), как и родной движок.
    """

    def __init__(self, scanner=None, families_json: Path | None = None):
        self._scanner = scanner
        value = os.environ.get("CV_FAMILIES_JSON")
        self._families_path = families_json or (Path(value).expanduser() if value else None)

    @cached_property
    def scanner(self):
        if self._scanner is not None:
            return self._scanner
        from winescan.service.pipeline import Scanner, ScannerConfig

        return Scanner(ScannerConfig.from_env())

    @cached_property
    def _family_of(self) -> dict[str, str]:
        """slug -> идентификатор near-dup семьи из переписи кейса, если она есть."""
        if not self._families_path or not self._families_path.exists():
            return {}
        try:
            raw = json.loads(self._families_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            log.warning("перепись семей не прочитана (%s): gap считается от следующего кандидата", error)
            return {}
        families = raw.get("families", raw) if isinstance(raw, dict) else raw
        mapping: dict[str, str] = {}
        if isinstance(families, dict):
            for family_id, members in families.items():
                for slug in members or ():
                    mapping[str(slug)] = str(family_id)
        elif isinstance(families, list):
            for position, entry in enumerate(families):
                members = entry.get("slugs", ()) if isinstance(entry, dict) else entry
                for slug in members or ():
                    mapping[str(slug)] = str(position)
        return mapping

    @property
    def index_version(self) -> str:
        """Версия галереи — имена индексов, по которым идёт поиск."""
        return "+".join(self.scanner.config.indexes)

    def search(self, image: bytes, top_k: int = 5, *, normalize: bool = True) -> list[Match]:
        """Поиск по каталогу.

        `normalize=False` (флаг A/B контракта) у нас означает «кадр целиком, детектор не
        запускать»: рамка задаётся долями всего кадра — тем же путём, которым приходит рамка
        пользователя.
        """
        result = self.scanner.scan(_decode(image), relative_box=None if normalize else WHOLE_FRAME)
        candidates = list(result.top5[:top_k])
        return [
            Match(slug=str(candidate["slug"]), score=float(candidate["score"]),
                  gap=self._gap(candidates, position), view="real")  # fmt: skip
            for position, candidate in enumerate(candidates)
        ]

    def _gap(self, candidates: list[dict], position: int) -> float | None:
        """Отрыв кандидата от ближайшего «чужого»: по переписи семей, если она есть, иначе от
        следующего в выдаче. `None` — конкурентов в выдаче не осталось (контрактное
        «доминирование»)."""
        current = candidates[position]
        family = self._family_of.get(str(current["slug"]))
        for other in candidates[position + 1:]:
            if family is None or self._family_of.get(str(other["slug"])) != family:
                return float(current["score"]) - float(other["score"])
        return None

    def embed(self, image: bytes) -> list[float]:
        raise NotImplementedError(
            "у движка winescan два индекса (упаковка и этикетка) и нет одного «вектора кадра». "
            "Для сравнения эмбеддингов пользуйтесь winescan.search.multi.MultiIndexSearcher."
        )

    def build(self, refs: dict[str, list[str]], version: str) -> None:
        raise NotImplementedError(
            "галерея собирается отдельной командой: python -m winescan.search.build_index "
            "--yaws=-30,-15,0,15,30 (см. packages/winescan/README.md). Адаптер — только на чтение."
        )

    def add(self, slug: str, images: list[bytes]) -> None:
        raise NotImplementedError(
            "пополнение галереи — та же команда сборки, с ключом --slugs; адаптер только читает."
        )


def _decode(image: bytes) -> Image.Image:
    """Байты -> картинка. Битый файл даёт ValueError, как требует контракт, а не 500."""
    try:
        return Image.open(io.BytesIO(image)).convert("RGB")
    except Exception as error:  # PIL кидает разные типы на разный мусор
        raise ValueError(f"не удалось прочитать изображение: {error}") from error


def get_image_index() -> WinescanImageIndex:
    """Фабричная функция того же образца, что `rag.get_retriever()`: `app/cv/factory.py`
    пробует её первой, поэтому ветка провайдера остаётся короткой."""
    return WinescanImageIndex()
