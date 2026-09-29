"""Обвязка SigLIP 2 (transformers) — cv/encoder.py, бриф п.4.

Единственная точка входа к модели: RGB ndarray -> L2-нормированный эмбеддинг.
Чекпойнт — env `CV_MODEL` (cv/config.py); выбор обоснован замером на MPS/CPU,
см. reports/g-report.md, раздел "Модель: выбор и обоснование".

Кэш эмбеддингов на диске (ключ — sha256 пиксельных байт + имя модели), чтобы повторные
build/bench/selfcheck не гоняли инференс на уже виденных картинках. `benchmark()`
специально обходит кэш (иначе меряем скорость dict-lookup, а не самой модели).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import AutoModel, AutoProcessor, __version__ as transformers_version

from cv import config

logger = logging.getLogger(__name__)
CACHE_SCHEMA = "rgb-auto-processor-v2"


def pick_device(requested: str | None = None) -> str:
    if requested:
        return requested
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class SiglipEncoder:
    """Ленивая загрузка модели — импорт модуля/конструктор не должны тянуть веса
    (тесты, не трогающие энкодер, не обязаны платить за это временем/сетью)."""

    def __init__(self, model_name: str | None = None, device: str | None = None, cache_dir: Path | None = None):
        self.model_name = model_name or config.CV_MODEL
        self.device = pick_device(device or config.CV_DEVICE)
        self.cache_dir = cache_dir or config.EMBED_CACHE_DIR
        self._model = None
        self._processor = None
        self._dim: int | None = None

    def _load(self) -> None:
        if self._model is not None:
            return
        self._processor = AutoProcessor.from_pretrained(self.model_name)
        model = AutoModel.from_pretrained(self.model_name)
        model.eval()
        model.to(self.device)
        self._model = model

    @property
    def dim(self) -> int:
        if self._dim is None:
            dummy = np.full((16, 16, 3), 128, dtype=np.uint8)
            self._dim = len(self.encode(dummy, use_cache=False))
        return self._dim

    def _cache_path(self, arr: np.ndarray) -> Path:
        header = json.dumps([CACHE_SCHEMA, transformers_version, arr.shape, arr.dtype.str]).encode()
        h = hashlib.sha256(header + b"\0" + arr.tobytes()).hexdigest()
        safe_model = self.model_name.replace("/", "__")
        return self.cache_dir / f"{safe_model}__{h}.json"

    def encode(self, image: np.ndarray, use_cache: bool = True) -> list[float]:
        """RGB uint8 ndarray -> L2-нормированный эмбеддинг. Размер/препроцессинг входа
        под модель делает HF `AutoProcessor` — сюда можно передавать любой валидный
        RGB-массив (нормализованный запрос стандартного канонического размера или,
        для `ImageIndex.embed()`, произвольный кроп)."""
        cache_path = self._cache_path(image) if use_cache else None
        if cache_path is not None:
            try:
                cached = json.loads(cache_path.read_text())
                if (isinstance(cached, list) and cached
                    and all(type(x) in (int, float) and np.isfinite(x) for x in cached)
                    and (self._dim is None or len(cached) == self._dim)):
                    return cached
            except (OSError, ValueError, UnicodeError, TypeError, OverflowError):
                pass  # A missing, unreadable or incomplete cache is a cache miss.

        self._load()
        pil = Image.fromarray(image, mode="RGB")
        inputs = self._processor(images=pil, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.no_grad():
            output = self._model.get_image_features(**inputs)
        # transformers 5.x: `get_image_features()` для Siglip2Model возвращает не голый
        # тензор (как в более старом CLIP-style API), а `BaseModelOutputWithPooling` —
        # нужный нам пулинг лежит в `.pooler_output`. Проверено эмпирически на
        # google/siglip2-base-patch16-224 (transformers==5.17.0); на случай будущей
        # версии/чекпойнта с иным поведением — плоский тензор тоже поддержан напрямую.
        feats = output if isinstance(output, torch.Tensor) else output.pooler_output
        vec = feats[0].to("cpu", dtype=torch.float32).numpy()
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        out = [float(x) for x in vec]
        if cache_path is not None:
            temporary = None
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", dir=self.cache_dir, suffix=".tmp", delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump(out, handle)
                os.replace(temporary, cache_path)
            except OSError:
                logger.warning("Embedding cache write failed; returning computed vector")
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
        return out

    def encode_batch(self, images: list[np.ndarray], use_cache: bool = True) -> list[list[float]]:
        return [self.encode(im, use_cache=use_cache) for im in images]

    def benchmark(self, images: list[np.ndarray], n: int | None = None) -> dict:
        """Тайминги `encode()` БЕЗ кэша — иначе на повторных фото мерили бы скорость
        dict-lookup, а не инференса. `n` может превышать len(images) — тогда список
        крутится по кругу (полезно замерить p95 на небольшом наборе фикстур)."""
        if not images:
            return {"device": self.device, "model": self.model_name, "n": 0}
        self._load()
        n = n or len(images)
        imgs = [images[i % len(images)] for i in range(n)]

        timings = []
        for im in imgs:
            t0 = time.perf_counter()
            self.encode(im, use_cache=False)
            timings.append((time.perf_counter() - t0) * 1000)

        arr = np.array(timings)
        return {
            "device": self.device,
            "model": self.model_name,
            "n": len(arr),
            "p50_ms": round(float(np.percentile(arr, 50)), 2),
            "p95_ms": round(float(np.percentile(arr, 95)), 2),
            "max_ms": round(float(arr.max()), 2),
            "mean_ms": round(float(arr.mean()), 2),
        }
