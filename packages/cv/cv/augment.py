"""Аугментатор 3D-ракурсов из одного эталонного фото (cv/augment.py, бриф п.2).

По мотивам arXiv:2404.08820 (синтез согласованных 3D-ракурсов из единственного
эталона через параметрическую поверхность) — классический CV, БЕЗ GAN и БЕЗ обучения:

1. Эталонное фото бутылки считаем текстурой, наклеенной на фронтальную дугу
   ЦИЛИНДРА (радиус ~ бутылочный, варьируется по seed).
2. Для каждого ракурса ставим виртуальную камеру в новую 3D-позицию вокруг цилиндра
   (yaw/pitch/roll по трём осям + дистанция + фокусное расстояние) и рендерим её вид
   через ray-casting: луч на пиксель выходного кадра -> пересечение с цилиндром ->
   (theta, y) на поверхности -> сэмплируем текстуру. Это честная перспективная
   проекция (не плоский cv2.warpPerspective) — угловые искажения по краям и light
   falloff по кривизне получаются из геометрии, а не нарисованы отдельным фильтром.
3. Поверх рендера — блики-полосы, расфокус/смаз, шум, композит с синтетическим фоном
   (полка/рука/нейтральный), вариации экспозиции.

Всё детерминировано одним `numpy.random.Generator(seed)`, параметры тянутся строго по
порядку (view 0, 1, ..., n-1) — тот же (image, n, seed) даёт побайтово тот же список
массивов при повторном вызове (тест: детерминизм по seed).

Фон синтезируется процедурно (numpy/cv2 примитивы), а не фотографиями со стока —
осознанный выбор: не тащить в репозиторий/пайплайн чужой лицензированный контент
только ради фона аугментации (см. reports/g-report.md, "Предположения").
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from cv import imageio, normalize

OUT_SIZE_DEFAULT = 448
VIEWS_DEFAULT = 24  # DoD: >= 20


def _standardize_texture(image: np.ndarray, max_dim: int = 640) -> np.ndarray:
    """Кроп к области этикетки (см. `cv.normalize.crop_to_label` — брифу буквально
    нужна "этикетка на цилиндр", не весь кадр бутылочного фото с пустым стеклом/
    горлышком) + приведение к разумному рабочему разрешению (быстрее рендер)."""
    image = normalize.crop_to_label(image)
    h, w = image.shape[:2]
    scale = max_dim / max(h, w)
    if scale < 1.0:
        image = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    return image


# --- геометрия ракурса ---------------------------------------------------------------


@dataclass(frozen=True)
class _ViewParams:
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    radius: float
    dist_factor: float  # дистанция камеры = radius * dist_factor
    fov_deg: float
    phi_total_deg: float  # угловая ширина видимой дуги текстуры на цилиндре
    light_yaw_deg: float
    light_elev_deg: float
    fill_frac: float  # доля высоты кадра, которую целится занять текстура анфас ("зум")


def _sample_view_params(rng: np.random.Generator) -> _ViewParams:
    """Диапазоны — эмпирические (типичная фото-съёмка бутылки на полке/в руке под
    умеренным углом — с этикетки всё ещё должно быть можно ЧТО-ТО прочитать, иначе
    ни один сканер, включая референс Vivino, на таком фото не работает), не из статьи
    буквально: подобраны так, чтобы дуга (phi_total) и дистанция гарантированно
    держали камеру снаружи цилиндра с запасом (дистанция >= 1.6*R) и не уводили
    ракурс за пределы видимой на эталоне стороны этикетки (|yaw| <= 28°,
    phi_total <= 95° — суммарно меньше 180°, задняя сторона бутылки не запрашивается,
    её и не было на исходном фото).

    `fill_frac` варьируется (0.55-0.88) — сколько кадра занимает бутылка/этикетка,
    т.е. "насколько близко сфотографировали". НЕ зафиксировано высоко для всех
    ракурсов — было багом ранней версии: этикетка всегда заливала ~85% кадра, а
    query-детектор `cv.normalize.detect_label_region` (тогда — на дискретных
    Canny-контурах/saliency-карте) настроен искал этикетку как МЕНЬШУЮ часть более
    крупного кадра и на кадре, уже залитом этикеткой под завязку, систематически
    проваливался в мусорный под-контур. Текущий детектор (силуэт по контрасту с
    фоном рамки кадра + нижние 2/3) устойчив к этому и на широком, и на узком плане —
    нижняя граница диапазона поднята обратно (0.4->0.55): self-match на дев-фикстурах
    оказался чувствителен к ДОЛЕ полезного контента в кадре относительно синтетического
    фона (см. reports/g-report.md, "Предположения" — SigLIP2-base различает эти
    конкретные этикетки не с огромным запасом, лишний фон в кадре ощутимо мешает)."""
    return _ViewParams(
        yaw_deg=rng.uniform(-24, 24),
        pitch_deg=rng.uniform(-10, 15),
        roll_deg=rng.uniform(-7, 7),
        radius=rng.uniform(0.8, 1.3),
        dist_factor=rng.uniform(1.6, 2.2),
        fov_deg=rng.uniform(35, 55),
        phi_total_deg=rng.uniform(55, 95),
        light_yaw_deg=rng.uniform(-25, 25),
        light_elev_deg=rng.uniform(10, 45),
        fill_frac=rng.uniform(0.55, 0.88),
    )


def render_cylinder_view(texture: np.ndarray, params: _ViewParams, out_size: int) -> tuple[np.ndarray, np.ndarray]:
    """Рендер `texture` (H,W,3 uint8), обёрнутой на цилиндр, под заданным ракурсом.

    Возвращает (rendered float32 [0,255] out_size×out_size×3 с нулями вне поверхности
    цилиндра, valid bool-маска той же формы без канала). Камера смотрит на цилиндр
    (ось Y) из точки на сфере радиуса `dist_factor*radius`; для каждого пикселя кадра
    луч пересекается с цилиндром x²+z²=R² (ближний корень — видимая снаружи сторона).
    """
    th, tw = texture.shape[:2]
    phi_total = math.radians(params.phi_total_deg)
    R = params.radius
    tex_h_world = th * (phi_total * R / tw)  # мировая высота текстуры — сохраняет пропорции

    yaw = math.radians(params.yaw_deg)
    pitch = math.radians(params.pitch_deg)
    roll = math.radians(params.roll_deg)
    D = R * params.dist_factor

    cam_pos = np.array(
        [
            D * math.sin(yaw) * math.cos(pitch),
            D * math.sin(pitch),
            D * math.cos(yaw) * math.cos(pitch),
        ],
        dtype=np.float64,
    )
    forward = -cam_pos / np.linalg.norm(cam_pos)
    world_up = np.array([0.0, 1.0, 0.0])
    right = np.cross(forward, world_up)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    right_r = right * math.cos(roll) + up * math.sin(roll)
    up_r = -right * math.sin(roll) + up * math.cos(roll)

    # fx — из желаемого FOV (управляет "объективом"/кривизной по горизонтали).
    # fy — ОТДЕЛЬНО, подогнан так, чтобы анфас (yaw=pitch=0) вписывал ПОЛНУЮ высоту
    # текстуры в кадр с запасом params.fill_frac ("зум" — насколько близко снято):
    # разный aspect ratio входных фото (узкие высокие бутылки vs широкие этикетки)
    # иначе даёт то катастрофический зум в маленький кусок текстуры, то наоборот —
    # избыточные поля. Анизотропные fx/fy — чисто рендер-приём (не грубее остальной
    # модели).
    fx = (out_size / 2.0) / math.tan(math.radians(params.fov_deg) / 2.0)
    near_depth = D - R  # глубина точки цилиндра прямо перед камерой (theta=0)
    fy = (out_size / 2.0 * params.fill_frac) * near_depth / (tex_h_world / 2.0)

    cx = cy = out_size / 2.0
    uu, vv = np.meshgrid(np.arange(out_size, dtype=np.float64), np.arange(out_size, dtype=np.float64))
    ndc_x = (uu - cx) / fx
    ndc_y = (vv - cy) / fy

    rd = (
        forward[None, None, :]
        + ndc_x[..., None] * right_r[None, None, :]
        - ndc_y[..., None] * up_r[None, None, :]  # минус: строки экрана растут вниз
    )
    rd = rd / np.linalg.norm(rd, axis=-1, keepdims=True)

    ox, oy, oz = cam_pos
    dx, dy, dz = rd[..., 0], rd[..., 1], rd[..., 2]

    a = dx * dx + dz * dz
    b = 2 * (ox * dx + oz * dz)
    c = ox * ox + oz * oz - R * R
    disc = b * b - 4 * a * c
    valid = disc >= 0
    sq = np.sqrt(np.clip(disc, 0, None))
    a_safe = np.where(np.abs(a) < 1e-12, 1e-12, a)
    t = (-b - sq) / (2 * a_safe)  # ближний корень = сторона цилиндра, видимая снаружи
    valid &= t > 1e-6

    hit_x = ox + t * dx
    hit_y = oy + t * dy
    hit_z = oz + t * dz

    theta = np.arctan2(hit_x, hit_z)
    valid &= np.abs(theta) <= (phi_total / 2)
    valid &= np.abs(hit_y) <= (tex_h_world / 2)

    x_tex = (theta / phi_total + 0.5) * (tw - 1)
    y_tex = (0.5 - hit_y / tex_h_world) * (th - 1)

    map_x = x_tex.astype(np.float32)
    map_y = y_tex.astype(np.float32)
    rendered = cv2.remap(
        texture, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0)
    ).astype(np.float32)

    # Ламбертова подсветка по кривизне — визуально отличает цилиндр от плоского warp:
    # края ракурса (большой |theta|) темнее центра, как на реальной глянцевой бутылке.
    normal_x = hit_x / R
    normal_z = hit_z / R
    light_yaw = math.radians(params.light_yaw_deg)
    light_elev = math.radians(params.light_elev_deg)
    light = np.array(
        [math.sin(light_yaw) * math.cos(light_elev), math.sin(light_elev), math.cos(light_yaw) * math.cos(light_elev)]
    )
    ndotl = normal_x * light[0] + normal_z * light[2]
    shade = 0.55 + 0.45 * np.clip(ndotl, 0.0, 1.0)
    rendered *= shade[..., None]
    rendered[~valid] = 0
    return rendered, valid


# --- синтетические фоны (полка/рука/нейтральный) --------------------------------------


def _flatten_border(bg: np.ndarray, border_frac: float = 0.06) -> np.ndarray:
    """Плавно сводит внешние `border_frac` кадра к среднему тону фона.

    cv.normalize.detect_label_region оценивает фон ИМЕННО по узкой рамке кадра
    (устройство камеры/детектора этикетки не знает, что происходит в глубине сцены) —
    полосатая "shelf"-подложка сама по себе реалистична, но если полоса-соседняя
    полка стыкуется ровно на рамке кадра, оценка фона по рамке становится
    непредсказуемой (кусок одной полосы, кусок другой). Реальные фото тоже почти
    всегда имеют мягкое виньетирование/боке к краю кадра — это не искусственное
    упрощение задачи, а физически правдоподобный штрих, который попутно чинит
    надёжность детектора (см. reports/g-report.md, "Предположения")."""
    out_size = bg.shape[0]
    mean_color = bg.reshape(-1, 3).astype(np.float32).mean(axis=0)
    yy, xx = np.mgrid[0:out_size, 0:out_size]
    dist_to_edge = np.minimum.reduce([xx, out_size - 1 - xx, yy, out_size - 1 - yy])
    blend = np.clip(dist_to_edge / max(1.0, out_size * border_frac), 0.0, 1.0)[..., None]
    out = bg.astype(np.float32) * blend + mean_color[None, None, :] * (1 - blend)
    return np.clip(out, 0, 255).astype(np.uint8)


def _make_plain_bg(out_size: int, rng: np.random.Generator) -> np.ndarray:
    base = rng.uniform(180, 235, size=3)
    grad = np.linspace(-1, 1, out_size)
    gx, gy = np.meshgrid(grad, grad)
    shade = 1.0 - 0.08 * (gx**2 + gy**2)
    bg = base[None, None, :] * shade[..., None]
    noise = rng.normal(0, 3, size=(out_size, out_size, 3))
    return np.clip(bg + noise, 0, 255).astype(np.uint8)


def _make_shelf_bg(out_size: int, rng: np.random.Generator) -> np.ndarray:
    hue = rng.uniform(0, 1)
    wood = np.array([120 + 60 * hue, 80 + 40 * hue, 50])
    bg = np.zeros((out_size, out_size, 3), dtype=np.float64)
    n_bands = int(rng.integers(3, 6))
    edges = np.sort(rng.uniform(0, out_size, size=n_bands - 1))
    edges = np.concatenate([[0], edges, [out_size]])
    for i in range(len(edges) - 1):
        y0, y1 = int(edges[i]), int(edges[i + 1])
        jitter = rng.uniform(-20, 20, size=3)
        bg[y0:y1, :, :] = np.clip(wood + jitter, 30, 230)
    for _ in range(int(rng.integers(0, 3))):
        cx = int(rng.integers(0, out_size))
        w = int(rng.integers(max(2, out_size // 12), max(3, out_size // 6)))
        h = int(rng.integers(out_size // 3, out_size))
        y0 = out_size - h
        color = rng.uniform(20, 90, size=3).tolist()
        cv2.rectangle(bg, (max(0, cx - w // 2), y0), (min(out_size, cx + w // 2), out_size), color, -1)
    bg = cv2.GaussianBlur(bg.astype(np.float32), (0, 0), sigmaX=out_size * 0.02)
    noise = rng.normal(0, 6, size=bg.shape)
    bg = np.clip(bg + noise, 0, 255).astype(np.uint8)
    return _flatten_border(bg)


def _make_hand_bg(out_size: int, rng: np.random.Generator) -> np.ndarray:
    bg = _make_plain_bg(out_size, rng).astype(np.float64)
    skin = np.array([rng.uniform(150, 220), rng.uniform(110, 170), rng.uniform(90, 140)])
    mask = np.zeros((out_size, out_size), dtype=np.float32)
    side = rng.choice(["left", "right"])
    cx = -out_size * 0.15 if side == "left" else out_size * 1.15
    cy = out_size * rng.uniform(0.5, 0.9)
    axes = (int(out_size * rng.uniform(0.35, 0.55)), int(out_size * rng.uniform(0.55, 0.8)))
    cv2.ellipse(mask, (int(cx), int(cy)), axes, 0, 0, 360, 1.0, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=out_size * 0.03)
    bg = bg * (1 - mask[..., None]) + skin[None, None, :] * mask[..., None]
    return np.clip(bg, 0, 255).astype(np.uint8)


def _make_background(out_size: int, rng: np.random.Generator) -> np.ndarray:
    mode = rng.choice(["shelf", "hand", "plain"], p=[0.5, 0.2, 0.3])
    if mode == "shelf":
        return _make_shelf_bg(out_size, rng)
    if mode == "hand":
        return _make_hand_bg(out_size, rng)
    return _make_plain_bg(out_size, rng)


def _composite(rendered: np.ndarray, valid: np.ndarray, background: np.ndarray) -> np.ndarray:
    mask = cv2.GaussianBlur(valid.astype(np.float32), (0, 0), sigmaX=1.2)[..., None]
    return rendered * mask + background.astype(np.float32) * (1 - mask)


# --- эффекты: блики, расфокус/смаз, шум, экспозиция -----------------------------------


def _add_glare(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    h, w = img.shape[:2]
    n = int(rng.integers(0, 3))
    overlay = np.zeros((h, w), dtype=np.float32)
    for _ in range(n):
        cx, cy = rng.uniform(0, w), rng.uniform(0, h)
        angle = rng.uniform(0, 180)
        length = rng.uniform(0.3, 0.9) * max(h, w)
        width = rng.uniform(0.03, 0.12) * max(h, w)
        strength = rng.uniform(0.15, 0.4)
        box = cv2.boxPoints(((cx, cy), (length, width), angle)).astype(np.int32)
        streak = np.zeros((h, w), dtype=np.float32)
        cv2.fillConvexPoly(streak, box, 1.0)
        streak = cv2.GaussianBlur(streak, (0, 0), sigmaX=width * 0.5 + 1)
        overlay = np.maximum(overlay, streak * strength)
    return np.clip(img.astype(np.float32) + overlay[..., None] * 255.0, 0, 255)


def _add_blur(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    img = img.astype(np.float32)
    if rng.random() < 0.5:
        sigma = rng.uniform(0.3, 1.4)
        return cv2.GaussianBlur(img, (0, 0), sigmaX=sigma)
    length = int(rng.integers(3, 7))
    angle = rng.uniform(0, 180)
    kernel = np.zeros((length, length), dtype=np.float32)
    kernel[length // 2, :] = 1.0
    m = cv2.getRotationMatrix2D((length / 2 - 0.5, length / 2 - 0.5), angle, 1.0)
    kernel = cv2.warpAffine(kernel, m, (length, length))
    s = kernel.sum()
    if s > 0:
        kernel /= s
    return cv2.filter2D(img, -1, kernel)


def _add_noise(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    sigma = rng.uniform(2, 6)
    return np.clip(img.astype(np.float32) + rng.normal(0, sigma, size=img.shape), 0, 255)


def _adjust_exposure(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    gain = rng.uniform(0.75, 1.25)
    bias = rng.uniform(-15, 15)
    gamma = rng.uniform(0.85, 1.2)
    x = np.clip(img.astype(np.float32) * gain + bias, 0, 255) / 255.0
    x = np.power(x, gamma)
    return np.clip(x * 255.0, 0, 255)


# --- публичное API ---------------------------------------------------------------------


def render_synthetic_views(
    image: np.ndarray, n: int = VIEWS_DEFAULT, seed: int = 0, out_size: int = OUT_SIZE_DEFAULT
) -> list[np.ndarray]:
    """>= 20 согласованных синтетических ракурсов из одного эталонного изображения.

    Детерминировано: тот же (image, n, seed) -> побайтово тот же список массивов при
    повторном вызове. Один `numpy.random.Generator(seed)` тянет параметры СТРОГО по
    порядку view 0..n-1 — никакого глобального `np.random`/скрытого источника энтропии.
    """
    texture = _standardize_texture(image)
    rng = np.random.default_rng(seed)
    views = []
    for _ in range(n):
        params = _sample_view_params(rng)
        rendered, valid = render_cylinder_view(texture, params, out_size)
        bg = _make_background(out_size, rng)
        composed = _composite(rendered, valid, bg)
        composed = _add_glare(composed, rng)
        composed = _add_blur(composed, rng)
        composed = _add_noise(composed, rng)
        composed = _adjust_exposure(composed, rng)
        views.append(np.clip(composed, 0, 255).astype(np.uint8))
    return views


def prepare_reference(image: np.ndarray, out_size: int = OUT_SIZE_DEFAULT) -> np.ndarray:
    """Детерминированная (без рандома) подготовка "реального" ракурса.

    Намеренно = `normalize.normalize_query()` (тот же детект+кроп+развёртка+фотометрия,
    что и для боевого запроса) — так "real"-вектор в индексе живёт в ТОМ ЖЕ визуальном
    домене, что и нормализованный запрос при поиске (а не "весь кадр бутылки" против
    "кроп этикетки" — рассинхрон домена бил бы self-match без всякой content-причины).
    """
    return normalize.normalize_query(image, enabled=True, out_size=out_size)


def save_synthetic_views(
    image_path: Path,
    out_dir: Path,
    slug: str,
    n: int = VIEWS_DEFAULT,
    seed: int = 0,
    out_size: int = OUT_SIZE_DEFAULT,
) -> list[Path]:
    """CLI-обвязка: рендерит и сохраняет JPEG синтетических ракурсов на диск.

    Возвращает пути СТРОГО в порядке synth-1..synth-n. Конвенция `ImageIndex.build()`
    (см. cv/index.py): для каждого slug список файлов = [эталон, *synth-пути] — первый
    элемент это "real", остальные — "synth-N" по позиции в списке.
    """
    image = imageio.load_image_file(str(image_path))
    views = render_synthetic_views(image, n=n, seed=seed, out_size=out_size)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, v in enumerate(views, start=1):
        p = out_dir / f"{slug}__synth-{i:02d}.jpg"
        p.write_bytes(imageio.encode_jpeg(v))
        paths.append(p)
    return paths
