#!/usr/bin/env python3
"""nt/make_dataset.py — наполнить `nt/dataset/` картинками-заглушками для прогона.

Нужно только чтобы нагрузочный тест было чем запустить «из коробки»: генерируются
синтетические фото этикеток реалистичного размера (по умолчанию 1200×1600, JPEG ~85,
сотни КБ) — такие же по весу и цветовому шуму, как снимок с телефона, чтобы время на
приём/декодирование фото на стороне API было честным.

ЭТО НЕ ТЕСТОВЫЙ НАБОР ДЛЯ ТОЧНОСТИ: картинки не соответствуют каталогу вин, они меряют
только производительность. Для реального прогона положите в `nt/dataset/` настоящие фото
с полки (см. dataset/README.md) — заглушки можно удалить.

Требуется Pillow (`pip install pillow`).

    python3 make_dataset.py                    # 8 картинок в ./dataset
    python3 make_dataset.py --count 40 --size 2000x2600 --out ./dataset-heavy
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent

WINERIES = ["Абрау-Дюрсо", "Alma Valley", "Фанагория", "Мысхако", "Château Tamagne",
            "Сикоры", "Золотая Балка", "Массандра"]
WINES = ["Каберне Совиньон", "Шардоне Резерв", "Рислинг сухое", "Мускат белый",
         "Пино Нуар", "Саперави", "Розе Брют", "Красностоп"]


def parse_size(text: str) -> tuple[int, int]:
    try:
        width, _, height = text.lower().partition("x")
        return int(width), int(height)
    except ValueError as exc:  # pragma: no cover — argparse покажет сообщение
        raise argparse.ArgumentTypeError(f"размер задаётся как ШИРИНАxВЫСОТА, например 1200x1600: {exc}")


def _font(size: int):
    from PIL import ImageFont

    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _fit_font(text: str, size: int, max_width: int):
    """Подбираем кегль, пока строка не влезет в этикетку — длинные названия иначе уезжают за край."""
    font = _font(size)
    while size > 10 and font.getbbox(text)[2] > max_width:
        size = int(size * 0.9)
        font = _font(size)
    return font


def make_photo(index: int, width: int, height: int, rng: random.Random):
    """Фон-«полка» с градиентом и шумом + наклонённая этикетка с текстом.

    Шум обязателен: гладкая заливка сжимается JPEG'ом в единицы КБ, и замер выродился бы
    в тест на пустом теле запроса вместо настоящей фотографии.
    """
    from PIL import Image, ImageDraw, ImageFilter

    base = Image.new("RGB", (width, height), (30, 24, 28))
    draw = ImageDraw.Draw(base)
    top = (rng.randint(40, 90), rng.randint(30, 70), rng.randint(35, 75))
    bottom = (rng.randint(120, 200), rng.randint(100, 180), rng.randint(90, 170))
    for y in range(height):  # вертикальный градиент — «свет витрины сверху»
        k = y / height
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(int(top[c] + (bottom[c] - top[c]) * k) for c in range(3)),
        )

    noise = Image.effect_noise((width, height), 28).convert("RGB")
    base = Image.blend(base, noise, 0.22).filter(ImageFilter.GaussianBlur(0.4))

    label_w, label_h = int(width * 0.62), int(height * 0.44)
    label = Image.new("RGB", (label_w, label_h), (rng.randint(225, 248),) * 3)
    ldraw = ImageDraw.Draw(label)
    accent = (rng.randint(90, 160), rng.randint(20, 60), rng.randint(40, 90))
    ldraw.rectangle([8, 8, label_w - 8, label_h - 8], outline=accent, width=5)
    ldraw.line([(40, int(label_h * 0.33)), (label_w - 40, int(label_h * 0.33))], fill=accent, width=3)

    winery = WINERIES[index % len(WINERIES)]
    wine = WINES[(index * 3) % len(WINES)]
    vintage = 2015 + (index % 9)
    max_text_w = label_w - 80
    ldraw.text((40, int(label_h * 0.14)), winery, font=_fit_font(winery, int(label_h * 0.11), max_text_w), fill=(35, 30, 35))
    ldraw.text((40, int(label_h * 0.42)), wine, font=_fit_font(wine, int(label_h * 0.13), max_text_w), fill=accent)
    line = f"{vintage} · 0,75 л · 13%"
    ldraw.text((40, int(label_h * 0.68)), line, font=_fit_font(line, int(label_h * 0.09), max_text_w), fill=(70, 65, 70))

    # Поворот вместе с маской: иначе углы, которыми rotate добивает холст, легли бы на фон
    # чёрными треугольниками.
    angle = rng.uniform(-7, 7)
    rotated = label.rotate(angle, expand=True, resample=Image.BICUBIC)
    mask = Image.new("L", (label_w, label_h), 255).rotate(angle, expand=True, resample=Image.BICUBIC)
    base.paste(rotated, ((width - rotated.size[0]) // 2, int(height * 0.28)), mask)

    return base.filter(ImageFilter.GaussianBlur(rng.uniform(0.0, 0.9)))  # лёгкий расфокус


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", "-n", type=int, default=8, help="сколько картинок сгенерировать")
    parser.add_argument("--size", type=parse_size, default="1200x1600", help="размер, ШИРИНАxВЫСОТА")
    parser.add_argument("--quality", type=int, default=85, help="качество JPEG")
    parser.add_argument("--out", type=Path, default=_SCRIPT_DIR / "dataset", help="куда писать")
    parser.add_argument("--seed", type=int, default=20260921, help="seed — воспроизводимый набор")
    args = parser.parse_args(argv)

    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("нужен Pillow: pip install pillow\n"
              "(либо просто положите настоящие фото в nt/dataset/ — генератор не обязателен)",
              file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    width, height = args.size
    total = 0
    for i in range(args.count):
        photo = make_photo(i, width, height, rng)
        path = args.out / f"sample-{i + 1:02d}.jpg"
        photo.save(path, "JPEG", quality=args.quality, optimize=False)
        total += path.stat().st_size
        print(f"  {path.name}  {path.stat().st_size / 1024:.0f} КБ")
    print(f"готово: {args.count} картинок, {total / 1024 / 1024:.1f} МБ в {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
