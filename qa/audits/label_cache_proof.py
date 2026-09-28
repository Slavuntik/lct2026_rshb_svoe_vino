"""Reproduce cache hazards without downloading models or changing production.

Run with the CV dependencies installed: python qa/audits/label_cache_proof.py.
After the 28.09 fixes, all three hazards must be absent. The original audit recorded them as true.
"""
from pathlib import Path
import json
import shutil
import tempfile

import numpy as np
import torch
from cv.encoder import SiglipEncoder


class Model:
    def get_image_features(self, **inputs):
        return torch.tensor([[3., 4.]])


def encoder(path):
    instance = SiglipEncoder(model_name='audit/fake', device='cpu', cache_dir=path)
    instance._model = Model()
    instance._processor = lambda **kwargs: {}
    return instance


def main():
    with tempfile.TemporaryDirectory() as temp:
        cache = Path(temp) / 'app' / '.embed_cache'
        instance = encoder(cache)
        image = np.arange(24, dtype=np.uint8).reshape(2, 4, 3)
        assert instance.encode(image)
        shutil.rmtree(cache)
        try:
            instance.encode(image)
        except FileNotFoundError:
            deletion_crash = True
        else:
            deletion_crash = False
        cache.mkdir(exist_ok=True)
        original = instance._cache_path(image)
        reshaped = instance._cache_path(image.reshape(4, 2, 3))
        original.write_text('{')  # Simulate an interrupted/non-atomic cache write.
        try:
            instance.encode(image)
        except json.JSONDecodeError:
            corruption_crash = True
        else:
            corruption_crash = False
        proof = {'cache_deletion_crashes_inference': deletion_crash,
                 'different_image_shapes_share_cache_key': original == reshaped,
                 'corrupt_cache_crashes_inference': corruption_crash}
        print(json.dumps(proof, indent=2))
        assert not any(proof.values()), 'Cache regression: recognition must survive cache failures'


if __name__ == '__main__':
    main()
