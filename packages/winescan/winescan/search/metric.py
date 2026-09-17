"""Слой 3: линейное преобразование эмбеддингов перед поиском (PLAN.md, 3.4).

Зачем. Шесть из десяти ошибок — путаница вин одной винодельни: дело не в нехватке признаков, а
в геометрии пространства SigLIP. Линейное отображение, обученное поверх **замороженных**
эмбеддингов (запросы — кропы синтетики, эталоны — векторы индексов), сближает кроп запроса с его
эталоном и разводит соседей по винодельне.

**В сервисе выключено, и включать нельзя без новых данных.** На винах, которых преобразование не
видело, оно даёт +10 п.п. top-1 в режиме сервиса (0,650 → 0,753) с переносом между генераторами
синтетики. Но обучение на **всех** винах — а в сервисе иначе не бывает — запоминает
синтетический рендер каждого вина: на единственном реальном фото из каталога верное вино
уезжает с 1-го места на 36-е. Низкий ранг не спасает (WORKLOG.md, раздел «3.4»).

Модуль оставлен для повторной проверки, когда появятся реальные размеченные фото: тогда в
обучении будет несколько разных снимков одного вина и запоминать один рендер станет нечего.

Применяется одинаково к векторам галереи (один раз при загрузке индексов) и к вектору запроса,
поэтому скоры остаются косинусами, но в новом пространстве: **пороги отказа из старого
пространства неприменимы**.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class Metric:
    """Матрица (rank, dim) и сведения о том, на чём она обучена."""

    weight: np.ndarray
    meta: dict

    @classmethod
    def load(cls, path: str | Path) -> Metric:
        data = np.load(Path(path), allow_pickle=False)
        meta = {key: data[key].tolist() for key in data.files if key != "weight"}
        return cls(np.asarray(data["weight"], dtype=np.float32), meta)

    @property
    def model_id(self) -> str:
        return str(self.meta.get("model_id", ""))

    @property
    def views(self) -> list[str]:
        views = self.meta.get("views", [])
        return [str(view) for view in views] if isinstance(views, list) else [str(views)]

    def check(self, model_id: str, view_names: list[str]) -> None:
        """Преобразование обучено для конкретной модели и порядка видов — иначе поиск молча испортится."""
        if self.model_id and self.model_id != model_id:
            raise ValueError(f"преобразование обучено для модели {self.model_id}, а индексы — {model_id}")
        trained_views = [view.split("__")[1] if "__" in view else "full" for view in self.views]
        if trained_views and len(trained_views) != len(view_names):
            raise ValueError(f"преобразование обучено для видов {trained_views}, а индексов {len(view_names)}")

    def project(self, vectors: np.ndarray) -> np.ndarray:
        """Проекция и нормировка: (n, dim) -> (n, rank)."""
        projected = np.asarray(vectors, dtype=np.float32) @ self.weight.T
        norms = np.linalg.norm(projected, axis=-1, keepdims=True)
        return projected / np.clip(norms, 1e-6, None)
