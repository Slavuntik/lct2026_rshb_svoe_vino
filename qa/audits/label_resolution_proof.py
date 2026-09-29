"""Demonstrate pixel loss from resize-before-user-crop; no model or dataset needed.

This is a diagnostic measurement, not a recognition-quality benchmark.
Run in the API environment: python qa/audits/label_resolution_proof.py.
"""
import io
import json
from PIL import Image
from app.cv.downscale import downscale_to_max_side
from app.cv.user_box import apply_user_box


def dimensions(data):
    with Image.open(io.BytesIO(data)) as image:
        return image.size


def main():
    buffer = io.BytesIO()
    Image.new('RGB', (4000, 3000), 'white').save(buffer, 'JPEG')
    data = buffer.getvalue()
    box = '0.4,0.4,0.6,0.6'
    current = dimensions(apply_user_box(downscale_to_max_side(data), box))
    crop_first = dimensions(downscale_to_max_side(apply_user_box(data, box)))
    assert current[0] < crop_first[0] and current[1] < crop_first[1]
    print(json.dumps({'current_resize_then_crop': current,
                      'alternative_crop_then_resize': crop_first,
                      'retained_pixel_ratio': (current[0]*current[1])/(crop_first[0]*crop_first[1])}, indent=2))


if __name__ == '__main__':
    main()
