"""Контактные листы (JPEG-сетки с подписями) для ручной проверки привязок фото."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from winescan.catalog.images import to_rgb_on_white

_FONT_PATHS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf")


def _font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


@dataclass
class Cell:
    path: Path | None
    caption: str
    highlight: bool = False


@dataclass
class SheetRow:
    title: str
    cells: list[Cell]


def render_sheets(
    rows: list[SheetRow], out_dir: Path, prefix: str, rows_per_sheet: int = 8, thumb: int = 230
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob(f"{prefix}_*.jpg"):
        stale.unlink()
    paths = []
    for start in range(0, len(rows), rows_per_sheet):
        path = out_dir / f"{prefix}_{start // rows_per_sheet + 1:02d}.jpg"
        _render(rows[start : start + rows_per_sheet], thumb).save(path, quality=85)
        paths.append(path)
    return paths


def _fit(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _render(rows: list[SheetRow], thumb: int) -> Image.Image:
    title_font, caption_font = _font(15), _font(12)
    pad, title_h, caption_h = 8, 24, 46
    max_cells = max(1, *(len(row.cells) for row in rows))
    width = max(1000, pad + max_cells * (thumb + pad))
    row_h = title_h + thumb + caption_h + pad
    sheet = Image.new("RGB", (width, row_h * len(rows)), "white")
    draw = ImageDraw.Draw(sheet)

    for row_index, row in enumerate(rows):
        y = row_index * row_h
        draw.rectangle([0, y, width, y + title_h], fill=(232, 232, 232))
        draw.text((pad, y + 4), _fit(draw, row.title, title_font, width - 2 * pad), fill="black", font=title_font)
        for cell_index, cell in enumerate(row.cells):
            x, top = pad + cell_index * (thumb + pad), y + title_h + 4
            if cell.path is not None:
                try:
                    with Image.open(cell.path) as image:
                        image.draft("RGB", (thumb * 2, thumb * 2))
                        preview = to_rgb_on_white(image)
                    preview.thumbnail((thumb, thumb))
                    sheet.paste(preview, (x + (thumb - preview.width) // 2, top + (thumb - preview.height) // 2))
                except Exception as exc:
                    draw.text((x + 4, top + 4), f"не открылось: {type(exc).__name__}", fill="red", font=caption_font)
            if cell.highlight:
                draw.rectangle([x - 3, top - 3, x + thumb + 2, top + thumb + 2], outline=(0, 150, 0), width=4)
            for line_index, line in enumerate(cell.caption.splitlines()[:3]):
                draw.text(
                    (x, top + thumb + 3 + line_index * 14),
                    _fit(draw, line, caption_font, thumb),
                    fill="black",
                    font=caption_font,
                )
    return sheet
