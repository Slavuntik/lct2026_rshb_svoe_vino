"""Разбор дефектов `apps/api/app/cv/downscale.py` (ml-lead, приёмка 648ad43).

Не метрика — точечные пробы на то, что метрика не ловит: EXIF-ориентация на
телефонном снимке, три формата входа (JPEG/WEBP/HEIC), совпадение прицела
пользователя до и после уменьшения, несгораемость на мусоре, читаемость мелкого
шрифта этикетки после уменьшения (OCR на кропе 1280 — как в бою).

  packages/cv/.venv/bin/python qa/real_photos_downscale_probe.py [--section all|exif|formats|box|junk|ocr]
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "apps" / "api"))

CASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
PHOTOS = CASE / "real-photos"
HEIC = CASE / "prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads"


def _mod(fresh: bool):
    """Модуль уменьшения. fresh=True — в процессе, где `cv.imageio` ещё НЕ импортирован
    (именно он регистрирует pillow-heif глобально, сам downscale.py этого не делает)."""
    from app.cv.downscale import downscale_to_max_side

    return downscale_to_max_side


def _open(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as im:
        return ImageOps.exif_transpose(im).convert("RGB")


def _diff(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.LANCZOS)
    return float(np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16)).mean())


def section_exif(ds) -> None:
    print("\n== EXIF-ориентация (телефонный снимок: пиксели лежат боком, поворот в теге) ==")
    src = sorted(PHOTOS.glob("*.webp"))[0]
    display = _open(src.read_bytes())  # портрет 3024x4032, как его видит человек
    ref_ok = display.resize((768, 1024), Image.Resampling.LANCZOS)
    # масштаб «что значит много»: тот же кадр, перевёрнутый — верхняя граница расхождения
    scale_bad = _diff(ref_ok, ref_ok.transpose(Image.Transpose.ROTATE_180))
    print(f"  эталон показа {display.size}; расхождение 'кадр перевёрнут' = {scale_bad:.1f} — это «много»")

    # все восемь значений тега: пиксели пишем так, чтобы exif_transpose вернул эталон
    inverse = {
        1: None,
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_90,   # обратная к тому, что применит exif_transpose
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_270,
    }
    for tag, op in inverse.items():
        stored = display if op is None else display.transpose(op)
        exif = Image.Exif()
        exif[0x0112] = tag
        buf = io.BytesIO()
        stored.save(buf, "JPEG", quality=95, exif=exif.tobytes())
        phone = buf.getvalue()
        assert _diff(display, _open(phone)) < 4.0, f"фикстура для тега {tag} неверна"
        got = _open(ds(phone, 1024))
        d = _diff(got, ref_ok)
        ok = got.size == (768, 1024) and d < 5
        print(f"  Orientation={tag} (пиксели {stored.size}) -> {str(got.size):12s} "
              f"расхождение с эталоном {d:5.2f}  {'ок' if ok else 'ДЕФЕКТ'}")

    # контроль: тот же кадр без тега вовсе
    buf = io.BytesIO(); display.save(buf, "JPEG", quality=95)
    got2 = _open(ds(buf.getvalue(), 1024))
    print(f"  без тега EXIF          -> {str(got2.size):12s} расхождение с эталоном {_diff(got2, ref_ok):5.2f}")
    # и контроль формата: WEBP-кадр кейса (EXIF там нет вовсе)
    got3 = _open(ds(src.read_bytes(), 1024))
    print(f"  исходный WEBP кейса    -> {str(got3.size):12s} расхождение с эталоном {_diff(got3, ref_ok):5.2f}")


def section_formats(ds) -> None:
    print("\n== Три формата входа: JPEG / WEBP / HEIC ==")
    src = sorted(PHOTOS.glob("*.webp"))[0]
    display = _open(src.read_bytes())
    samples: list[tuple[str, bytes]] = [("WEBP (эталонные фото кейса)", src.read_bytes())]
    b = io.BytesIO(); display.save(b, "JPEG", quality=95); samples.append(("JPEG", b.getvalue()))
    b = io.BytesIO(); display.save(b, "PNG"); samples.append(("PNG", b.getvalue()))
    heics = sorted(HEIC.glob("*.HEIC")) + sorted(HEIC.glob("*.heic"))
    big_heic = None
    for h in heics:
        try:
            with Image.open(h) as im:
                if max(im.size) > 1024:
                    big_heic = (h, h.read_bytes())
                    break
        except Exception:
            continue
    if big_heic:
        samples.append((f"HEIC ({big_heic[0].name}, {Image.open(io.BytesIO(big_heic[1])).size})", big_heic[1]))
    else:
        print("  HEIC больше 1024 px не найден на диске")

    for name, data in samples:
        t = time.perf_counter()
        out = ds(data, 1024)
        ms = (time.perf_counter() - t) * 1000
        try:
            with Image.open(io.BytesIO(out)) as im:
                size, fmt = im.size, im.format
        except Exception as e:
            size, fmt = None, f"нечитаемо: {e}"
        changed = out is not data
        print(f"  {name:48s} -> {str(size):14s} {str(fmt):5s} уменьшен={changed} {ms:7.1f} мс")


def section_box(ds) -> None:
    print("\n== Прицел пользователя: рамка в долях кадра ДО и ПОСЛЕ уменьшения ==")
    from app.cv.user_box import apply_user_box

    src = sorted(PHOTOS.glob("*.webp"))[0]
    raw = src.read_bytes()
    box = "0.20,0.30,0.80,0.70"
    before = _open(apply_user_box(raw, box))                # рамка по исходному кадру
    after = _open(apply_user_box(ds(raw, 1024), box))       # рамка по уменьшенному (как в бою)
    print(f"  кроп из исходного кадра: {before.size}; кроп из уменьшенного: {after.size}")
    print(f"  доли сторон: до {before.width / before.height:.4f}, после {after.width / after.height:.4f}")
    print(f"  расхождение содержимого (после ресайза к общему размеру): {_diff(before, after):.2f} (0..255)")
    shifted = before.crop((int(before.width * 0.03), 0, before.width, before.height))
    print(f"  для масштаба — сдвиг прицела на 3% ширины дал бы: {_diff(before, shifted):.2f}")


def section_junk(ds) -> None:
    print("\n== Несгораемость: мусор и моковые байты ==")
    cases = {
        "MOCKPHOTO:...": b"MOCKPHOTO:shato-vymysel-cabernet",
        "обрезанный JPEG": b"\xff\xd8\xff\xe0garbage",
        "пусто": b"",
        "текст": "не картинка вовсе".encode(),
        "PNG-заголовок без тела": b"\x89PNG\r\n\x1a\n",
    }
    src = sorted(PHOTOS.glob("*.webp"))[0]
    body = src.read_bytes()
    cases["обрезанный WEBP (половина файла)"] = body[: len(body) // 2]
    # «бомба»: маленький файл, огромный кадр
    bomb = io.BytesIO(); Image.new("RGB", (14000, 14000), (1, 2, 3)).save(bomb, "PNG")
    cases[f"PNG 14000x14000 ({len(bomb.getvalue()) // 1024} КБ)"] = bomb.getvalue()
    for name, data in cases.items():
        try:
            out = ds(data, 1024)
            same = out is data
            try:
                with Image.open(io.BytesIO(out)) as im:
                    size = im.size
            except Exception:
                size = None
            print(f"  {name:38s} -> исключения нет, отдан как был={same}, размер={size}")
        except Exception as e:
            print(f"  {name:38s} -> ИСКЛЮЧЕНИЕ {type(e).__name__}: {e}")


def section_ocr(ds, limit: int, only: list[str], show_text: bool = False) -> None:
    print("\n== Мелкий шрифт: OCR по кропу этикетки 1280 px, исходный кадр против уменьшенного ==")
    from cv.verify import LabelVerifier

    verifier = LabelVerifier(engine="rapid", rapid_sizes=(640, 960))
    rows = []
    picked = ([f for f in sorted(PHOTOS.glob("*.webp")) if any(o in f.name for o in only)]
              if only else sorted(PHOTOS.glob("*.webp"))[:limit])
    for p in picked:
        raw = p.read_bytes()
        small = ds(raw, 1024)
        t0 = time.perf_counter(); full = verifier.read_query_text(raw) or ""; t_full = time.perf_counter() - t0
        t0 = time.perf_counter(); down = verifier.read_query_text(small) or ""; t_down = time.perf_counter() - t0
        rows.append((p.name, len(full), len(down), t_full, t_down, full, down))
        print(f"  {p.name[:34]:34s} символов {len(full):4d} -> {len(down):4d}   {t_full:5.2f}с -> {t_down:5.2f}с")
        if show_text:
            print(f"      исходный кадр: {full}")
            print(f"      уменьшенный:   {down}")
    if rows:
        print(f"  ИТОГО символов текста: {sum(r[1] for r in rows)} -> {sum(r[2] for r in rows)} "
              f"({sum(r[2] for r in rows) / max(sum(r[1] for r in rows), 1):.1%} от исходного)")
        print(f"  ИТОГО время OCR: {sum(r[3] for r in rows):.1f}с -> {sum(r[4] for r in rows):.1f}с")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", default="all",
                    choices=["all", "exif", "formats", "box", "junk", "ocr"])
    ap.add_argument("--ocr-limit", type=int, default=8)
    ap.add_argument("--photos", nargs="*", default=[], help="подстроки имён фото для секции ocr")
    ap.add_argument("--show-text", action="store_true")
    a = ap.parse_args()
    ds = _mod(fresh=True)
    if a.section in ("all", "exif"):
        section_exif(ds)
    if a.section in ("all", "formats"):
        section_formats(ds)
    if a.section in ("all", "box"):
        section_box(ds)
    if a.section in ("all", "junk"):
        section_junk(ds)
    if a.section in ("all", "ocr"):
        section_ocr(ds, a.ocr_limit, a.photos, a.show_text)


if __name__ == "__main__":
    main()
