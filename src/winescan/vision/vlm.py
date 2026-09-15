"""Слой 2: чтение полей этикетки мультимодальной моделью (Qwen3-VL-4B-Instruct, Apache-2.0).

Модель читает кроп упаковки и возвращает JSON с полями; сверка с каталогом — search.fields.
По обзору (docs/RESEARCH.md, раздел 4) русский — слабое место VLM, поэтому модель вызывается
только для перестановки близких кандидатов, а не для поиска по каталогу.
"""

from __future__ import annotations

import time

import torch
from PIL import Image

from winescan.search.fields import LabelFields, parse_fields
from winescan.vision.embedder import default_device

DEFAULT_VLM = "Qwen/Qwen3-VL-4B-Instruct"
MAX_IMAGE_SIDE = 768

PROMPT = (
    "На фото бутылка вина. Прочитай этикетку этой бутылки и верни только JSON в одну строку, без пояснений:\n"
    '{"winery": "производитель", "name": "название вина", "grapes": ["сорт"], "year": 2023, '
    '"color": "белое|красное|розовое|оранжевое", '
    '"sweetness": "брют натюр|экстра брют|брют|сухое|полусухое|полусладкое|сладкое", '
    '"sparkling": false, "text": "до 12 самых крупных слов этикетки"}\n'
    "Пиши только то, что написано на этикетке. Цвет и сладость — только если они написаны. "
    "Если поле не видно — null. Ничего не придумывай."
)


class LabelFieldReader:
    # 160 токенов не хватало: у фото Массандры JSON обрезался посреди поля text
    def __init__(self, model_id: str = DEFAULT_VLM, device: str | None = None, max_new_tokens: int = 256):
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

        self.device = device or default_device()
        dtype = torch.bfloat16 if self.device.startswith("cuda") else torch.float32
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(model_id, dtype=dtype).to(self.device).eval()
        self.max_new_tokens = max_new_tokens
        self.last_ms = 0.0
        self.last_raw = ""

    @torch.inference_mode()
    def read(self, image: Image.Image) -> LabelFields:
        began = time.perf_counter()
        image = image.convert("RGB")
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        messages = [{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": PROMPT}]}]
        inputs = self.processor.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt"
        ).to(self.device)
        output = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens, do_sample=False)
        self.last_raw = self.processor.batch_decode(output[:, inputs["input_ids"].shape[1] :], skip_special_tokens=True)[0]
        self.last_ms = (time.perf_counter() - began) * 1000
        return parse_fields(self.last_raw)
