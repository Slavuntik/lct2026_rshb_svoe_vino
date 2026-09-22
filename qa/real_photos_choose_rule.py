"""Офлайн-проверка CV_FUSION_CHOOSE (задача тимлида 22.09): локальный путь (CV+OCR) и
модель (27B через шлюз) читают этикетку ОДНОВРЕМЕННО; модель не ответила за 6 с —
локальный ответ; ответила — выбираем ЛУЧШИЙ из двух готовых ответов. ml-engineer
реализует "оба ответа" + флаг `CV_FUSION_CHOOSE` в `service.py` (не эта зона); здесь —
офлайн-версия ровно тех же четырёх правил на кэшах `features/`, ЧЕРЕЗ БОЕВУЮ
`cv.text_fusion.fuse()` (не переизобретение формулы): `final = cv + 0.3*rel`, гейт
«не подтверждена винодельня» (только для text_source="ocr", CV_FUSION_OCR_UNCONFIRMED_W
=0.5; для vlm*-источников гейт без эффекта, w=1.0 — ровно как `app/cv/service.py::
_run_photo_scan_fusion`), штраф цвета 0.05. CV: индекс `base384_d1` (тот же
cv_index_version, что стенд), max(norm, raw). Кандидаты слияния мимикрируют
`ImageIndex.search_fusion()`: CV top-50 слагов ∪ ТОЧНЫЙ CV-скор текстовых top-30 (не
approx ANN, не заглушка cv_pad — она сама сработает внутри fuse() только для слагов
ВООБЩЕ без эталона в индексе, см. docstring fuse()).

Три готовых ответа на фото:
  local  = fuse(CV, OCR-текст)            — RapidOCR 640+960 (box_thresh=0.3/unclip=2.0,
                                             код-дефолты после hack-v9) + кроп этикетки
                                             1280, через пробел — ровно RapidOcrReader.read()
                                             (features/ocr_rapidS_640+ocr_rapidS_960+
                                             ocr_lab_rapidS_1280.jsonl).
  model  = fuse(CV, VLM-текст)            — 27B через шлюз, тот же промпт/парсинг что
                                             `app/cv/vision_llm.py` (features/ocr_vlm.jsonl).
  merge  = fuse(CV, VLM-текст+" "+OCR)    — боевой CV_FUSION_MERGE_MODEL_TEXT=1 (сейчас
                                             в бою); при пустом VLM — фолбэк на чистый OCR
                                             (гейт 0.5), бит-в-бит равно local.

Правила выбора (CV_FUSION_CHOOSE), из local/model (НЕ из merge — merge это отдельная,
пятая стратегия "склеить тексты", не "выбрать один из двух готовых ответов"):
  merge          — см. выше, отдельный fuse()-вызов.
  max_score      — top1 источника с большим final_score (cv+w*rel) его собственного топа.
  agree_else_llm — совпали → тот слаг; не совпали → ответ модели.
  agree_else_cv  — совпали → тот слаг; не совпали → локальный (CV+OCR) ответ.
  confident_else_cv (своё, с порогом) — модель её же СОБСТВЕННЫЙ гейт уверенности
    `result.confident` (gap_floor=0.03 И cv_score>=0.80 — уже откалиброванный в проде
    порог, НЕ подогнан под эти 62 фото, см. cv/text_fusion.py докстринг "Гейт
    уверенности") очищает → ответ модели; иначе → локальный ответ.

ВАЖНО (мат. факт, не гипотеза): "agree_else_llm" ТОЖДЕСТВЕННО "model" по топ-1 слагу
на КАЖДОМ фото (если совпали — общее значение это и есть model.top1; не совпали — тоже
берём model.top1 явно), а "agree_else_cv" ТОЖДЕСТВЕННО "local" — для функции "какой
слаг выбрать" ветка "agree" всегда не отличима от одной из веток "else" по построению
(h(x):=f(x) если f(x)==g(x) иначе g(x) ⟹ h≡g тождественно). Правила посчитаны и подписаны
раздельно (как их назвал тимлид), но численно это гарантированно = model / = local.

"Модель не ответила" (6-секундный дедлайн Вячеслава) — ЯВНАЯ проверка ДО применения
правила: пустой vlm-текст ⟹ ответ = local, независимо от CV_FUSION_CHOOSE (не считаем
"confident"/"score" по пустому тексту — это была бы уже другая политика, не та, что
тимлид описал). `merge` этого не требует отдельно — при пустом vlm merge_text=OCR,
гейт=0.5 = ТОТ ЖЕ вызов fuse(), что и local (проверено побитово, см. `--dead-model`).

Запуск (из packages/cv): .venv/bin/python ../../qa/real_photos_choose_rule.py
  --dead-model        — модель не отвечает НИ НА ОДНОМ фото (все правила должны дать
                         ровно local)
  --corrupt METHOD FRAC [FRAC ...] — заменить vlm-текст на чужой (METHOD=shuffle|niche)
                         на FRAC доле фото (0.10/0.30/1.0), сравнить с чистым
  --no-alias          — гейт винодельни БЕЗ case-data/winery_aliases.json (сверка с
                         живым hack-v12, где алиас-файл ещё не был на стенде)
  --none              — таблица смены ответа на 38 фото без вина в каталоге
  --dump-json PATH     — сохранить детальный результат (вне git)
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

import numpy as np

from cv import families as cv_families
from cv import text_fusion

BASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
LAB = BASE / "real-photos-labels"
FEAT = LAB / "features"
CSV_PATH = BASE / "strapi_output0709.csv"

W = 0.3
COLOR_PENALTY = 0.05
OCR_UNCONFIRMED_W = 0.5  # CV_FUSION_OCR_UNCONFIRMED_W — только text_source == "ocr"
MODEL_UNCONFIRMED_W = 1.0  # CV_FUSION_UNCONFIRMED_WINERY_W — vlm/vlm_local/vlm_both, без эффекта
ANN_TOP_K = text_fusion.DEFAULT_ANN_TOP_K  # 50
TEXT_TOP_N = text_fusion.DEFAULT_TEXT_TOP_N  # 30

RULES = ("merge", "max_score", "agree_else_llm", "agree_else_cv", "confident_else_cv")


# --------------------------------------------------------------------------------------
# Данные
# --------------------------------------------------------------------------------------


def load_labels():
    photos = json.loads((FEAT / "photos.json").read_text())
    idx_of = {p: i for i, p in enumerate(photos)}
    labels = {}
    for f in sorted(LAB.glob("part[12].csv")):
        with f.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                labels[r["photo"]] = r["true_slug"]
    in_cat = [p for p in photos if labels[p] not in ("", "NONE")]
    none_photos = [p for p in photos if labels[p] == "NONE"]
    assert len(in_cat) + len(none_photos) == len(photos) == 100
    return photos, idx_of, labels, in_cat, none_photos


def load_cv(n_photos):
    meta = json.loads((FEAT / "index_meta_base384_d1.json").read_text())
    vs = meta["slugs"]
    slugs = sorted(set(vs))
    pos = {s: i for i, s in enumerate(slugs)}
    vsi = np.array([pos[s] for s in vs])
    V = np.load(FEAT / "index_vectors_base384_d1.npy").astype(np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    Q = np.load(FEAT / "qemb_base384.npz")
    CV = np.full((n_photos, len(slugs)), -1.0, dtype=np.float32)
    for k in ("norm", "raw"):  # max(нормализованный кроп, весь кадр) — text_fusion.py докстринг "cv"
        q = Q[k].astype(np.float32)
        q /= np.linalg.norm(q, axis=1, keepdims=True)
        S = q @ V.T
        tmp = np.full((n_photos, len(slugs)), -1.0, dtype=np.float32)
        for i in range(n_photos):
            row = np.full(len(slugs), -1.0, dtype=np.float32)
            np.maximum.at(row, vsi, S[i])
            tmp[i] = row
        CV = np.maximum(CV, tmp)
    return CV, slugs, pos


def load_jsonl_text(tag):
    f = FEAT / f"ocr_{tag}.jsonl"
    return {json.loads(l)["photo"]: json.loads(l)["text"] for l in f.read_text().splitlines() if l.strip()}


def build_ocr_text(photos):
    """RapidOcrReader.read() бит-в-бит: sizes=(640,960) + label_size=1280, через пробел,
    только непустые части (packages/cv/cv/ocr_rapid.py::read(), строки 402-418)."""
    a, b, c = load_jsonl_text("rapidS_640"), load_jsonl_text("rapidS_960"), load_jsonl_text("lab_rapidS_1280")
    return {p: " ".join(t for t in (a.get(p, ""), b.get(p, ""), c.get(p, "")) if t.strip()) for p in photos}


# --------------------------------------------------------------------------------------
# Слияние — боевая формула (packages/cv/cv/text_fusion.py), не переизобретаем
# --------------------------------------------------------------------------------------


class Fuser:
    def __init__(self, CV, slugs, pos, use_alias=True):
        self.CV, self.slugs, self.pos = CV, slugs, pos
        self.text_index = text_fusion.load_catalog_index(str(CSV_PATH))
        aliases = str(BASE / "winery_aliases.json") if use_alias else "/nonexistent/winery_aliases.json"
        self.winery_index = text_fusion.load_winery_index(str(CSV_PATH), aliases_json=aliases)
        self.colors = text_fusion.color_by_slug(self.text_index)
        self.fam = cv_families.load_family_by_slug(BASE / "families.json")

    def _cv_scores(self, i, text_top_slugs):
        """Мимикрия ImageIndex.search_fusion(): CV top-K по слагам ∪ ТОЧНЫЙ CV-скор
        текстовых extra_slugs (docstring fuse(): "точным фильтрованным запросом Qdrant,
        не ANN-топ") — у нас плотная матрица, точный скор уже есть для любого
        индексированного слага, стаб cv_pad остаётся только для слагов ВООБЩЕ вне
        индекса (см. fuse())."""
        row = self.CV[i]
        order = np.argsort(-row)[:ANN_TOP_K]
        out = {self.slugs[j]: float(row[j]) for j in order}
        for s in text_top_slugs:
            if s in self.pos and s not in out:
                out[s] = float(row[self.pos[s]])
        return out

    def fuse(self, photo_idx: int, text: str, *, unconfirmed_w: float) -> text_fusion.FusionResult:
        _, mass = self.text_index.scores(text)
        text_top = text_fusion.top_text_slugs(self.text_index, mass, TEXT_TOP_N)
        return text_fusion.fuse(
            self._cv_scores(photo_idx, text_top), self.text_index, text, family_by_slug=self.fam, w=W,
            winery_index=self.winery_index, unconfirmed_winery_w=unconfirmed_w,
            colors=self.colors, color_penalty=COLOR_PENALTY,
        )

    def local(self, i, ocr_text):
        return self.fuse(i, ocr_text, unconfirmed_w=OCR_UNCONFIRMED_W)

    def model(self, i, vlm_text):
        return self.fuse(i, vlm_text, unconfirmed_w=MODEL_UNCONFIRMED_W)

    def merge(self, i, vlm_text, ocr_text):
        if vlm_text.strip():
            return self.fuse(i, f"{vlm_text} {ocr_text}".strip(), unconfirmed_w=MODEL_UNCONFIRMED_W)
        return self.fuse(i, ocr_text, unconfirmed_w=OCR_UNCONFIRMED_W)  # фолбэк — как _fusion_text_and_vectors


# --------------------------------------------------------------------------------------
# Правила CV_FUSION_CHOOSE
# --------------------------------------------------------------------------------------


def top1(r: "text_fusion.FusionResult") -> str | None:
    return r.ranked[0].slug if r.ranked else None


def score1(r: "text_fusion.FusionResult") -> float:
    return r.ranked[0].final_score if r.ranked else float("-inf")


def decide(rule: str, local: "text_fusion.FusionResult", model: "text_fusion.FusionResult",
           merge: "text_fusion.FusionResult", model_answered: bool) -> str | None:
    if rule == "merge":
        return top1(merge)
    if not model_answered:  # дедлайн 6 с Вячеслава — локальный ответ, ЛЮБОЕ правило
        return top1(local)
    if rule == "max_score":
        return top1(model) if score1(model) > score1(local) else top1(local)
    if rule == "agree_else_llm":
        return top1(model)  # тождественно local, если совпали — см. докстринг модуля
    if rule == "agree_else_cv":
        return top1(local)
    if rule == "confident_else_cv":
        return top1(model) if model.confident else top1(local)
    raise ValueError(rule)


# --------------------------------------------------------------------------------------
# Заражение чтения модели (устойчивость к галлюцинации)
# --------------------------------------------------------------------------------------


def _derangement(n: int, rng: random.Random) -> list[int]:
    if n < 2:
        return list(range(n))
    while True:
        perm = list(range(n))
        rng.shuffle(perm)
        if all(perm[i] != i for i in range(n)):
            return perm


def corrupt_shuffle(vlm_text: dict, in_cat: list, fraction: float, seed: int):
    """Перестановка чтений МЕЖДУ фото — дерандж внутри заражённого подмножества
    (ни одно фото не оставляет своё же чтение); при размере подмножества 1 — донор
    случайный из ОСТАЛЬНЫХ (дерандж вырожден)."""
    rng = random.Random(seed)
    n = round(fraction * len(in_cat))
    victims = rng.sample(in_cat, n)
    out = dict(vlm_text)
    if n == 1:
        donor = rng.choice([q for q in in_cat if q != victims[0]])
        out[victims[0]] = vlm_text.get(donor, "")
    elif n >= 2:
        perm = _derangement(n, rng)
        src_texts = [vlm_text.get(victims[j], "") for j in range(n)]
        for i, p in enumerate(victims):
            out[p] = src_texts[perm[i]]
    return out, set(victims)


def corrupt_niche(vlm_text: dict, in_cat: list, labels: dict, text_index, fraction: float, seed: int):
    """Чтение похожей по нише бутылки — донор той же (category, sugar), что истина
    жертвы, НО другого слага; при отсутствии — той же category; при отсутствии —
    случайный (считается и логируется как fallback)."""
    rng = random.Random(seed)
    n = round(fraction * len(in_cat))
    victims = rng.sample(in_cat, n)

    def niche(p):
        e = text_index.extra.get(labels[p], {})
        return e.get("category", ""), e.get("sugar", "")

    out = dict(vlm_text)
    fallback = 0
    for p in victims:
        truth = labels[p]
        nch = niche(p)
        pool = [q for q in in_cat if q != p and labels[q] != truth]
        same_niche = [q for q in pool if nch[0] and niche(q) == nch]
        same_cat = [q for q in pool if nch[0] and niche(q)[0] == nch[0]] if not same_niche else []
        chosen_pool = same_niche or same_cat or pool
        if not same_niche and not same_cat:
            fallback += 1
        donor = sorted(chosen_pool)[rng.randrange(len(chosen_pool))]
        out[p] = vlm_text.get(donor, "")
    return out, set(victims), fallback


# --------------------------------------------------------------------------------------
# Оценка
# --------------------------------------------------------------------------------------


def evaluate(fs: Fuser, idx_of, labels, in_cat, ocr_text, vlm_text, model_answered=None):
    if model_answered is None:
        model_answered = {p: bool(vlm_text.get(p, "").strip()) for p in in_cat}
    acc = {r: 0 for r in RULES}
    preds = {r: {} for r in RULES}
    for p in in_cat:
        i = idx_of[p]
        local_r = fs.local(i, ocr_text[p])
        answered = model_answered[p]
        model_r = fs.model(i, vlm_text.get(p, "")) if answered else None
        merge_r = fs.merge(i, vlm_text.get(p, "") if answered else "", ocr_text[p])
        truth = labels[p]
        for rule in RULES:
            pred = decide(rule, local_r, model_r, merge_r, answered)
            preds[rule][p] = pred
            acc[rule] += pred == truth
    n = len(in_cat)
    return {r: acc[r] / n for r in RULES}, preds, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-alias", action="store_true", help="гейт винодельни без case-data/winery_aliases.json")
    ap.add_argument("--dead-model", action="store_true", help="модель не отвечает нигде — все правила = local")
    ap.add_argument("--corrupt", nargs="+", default=[], help="METHOD FRAC [FRAC...], METHOD=shuffle|niche")
    ap.add_argument("--none", action="store_true", help="смена ответа на 38 NONE-фото")
    ap.add_argument("--seed", type=int, default=20260922)
    ap.add_argument("--dump-json", default="")
    a = ap.parse_args()

    photos, idx_of, labels, in_cat, none_photos = load_labels()
    CV, slugs, pos = load_cv(len(photos))
    fs = Fuser(CV, slugs, pos, use_alias=not a.no_alias)
    ocr_text = build_ocr_text(photos)
    vlm_text = load_jsonl_text("vlm")
    print(f"фото: {len(photos)}; в каталоге (sure+likely): {len(in_cat)}; NONE: {len(none_photos)}; "
          f"winery_aliases.json: {'выкл' if a.no_alias else 'вкл'}")

    # --- baseline: local / model в чистом виде (для сверки формулы) ---
    hit_local = hit_model = 0
    for p in in_cat:
        i = idx_of[p]
        hit_local += top1(fs.local(i, ocr_text[p])) == labels[p]
        hit_model += top1(fs.model(i, vlm_text.get(p, ""))) == labels[p]
    n = len(in_cat)
    print(f"\nсверка формулы (62 фото):")
    print(f"  local (CV+OCR)      {hit_local}/{n} = {hit_local / n:.1%}")
    print(f"  model (CV+27B vlm)  {hit_model}/{n} = {hit_model / n:.1%}")

    clean_acc, clean_preds, n = evaluate(fs, idx_of, labels, in_cat, ocr_text, vlm_text)
    print(f"\nправила CV_FUSION_CHOOSE, чисто (62 фото, n={n}):")
    for r in RULES:
        print(f"  {r:18s} {round(clean_acc[r] * n)}/{n} = {clean_acc[r]:.1%}")

    if a.dead_model:
        dead_vlm = {p: "" for p in in_cat}
        dead_acc, dead_preds, _ = evaluate(fs, idx_of, labels, in_cat, ocr_text, dead_vlm,
                                            model_answered={p: False for p in in_cat})
        print(f"\nмодель мертва (vlm-текст пуст на всех {n} фото):")
        local_acc = hit_local / n
        for r in RULES:
            same = all(dead_preds[r][p] == dead_preds["agree_else_cv"][p] for p in in_cat)
            print(f"  {r:18s} {round(dead_acc[r] * n)}/{n} = {dead_acc[r]:.1%}"
                  f"{'  == local' if abs(dead_acc[r] - local_acc) < 1e-9 else '  !! != local'}"
                  f"{'  (побитово = local)' if same else '  (!! ответы разошлись с local)'}")

    if a.corrupt:
        method = a.corrupt[0]
        assert method in ("shuffle", "niche"), f"--corrupt: METHOD должен быть shuffle|niche, получено {method!r}"
        fracs = [float(x) for x in a.corrupt[1:]] or [0.10, 0.30, 1.0]
        print(f"\nустойчивость к галлюцинации — метод {method}:")
        for frac in fracs:
            if method == "shuffle":
                cv_text, victims = corrupt_shuffle(vlm_text, in_cat, frac, a.seed)
                fb_note = ""
            else:
                cv_text, victims, fb = corrupt_niche(vlm_text, in_cat, labels, fs.text_index, frac, a.seed)
                fb_note = f" (fallback случайный донор: {fb}/{len(victims)})"
            acc, preds, _ = evaluate(fs, idx_of, labels, in_cat, ocr_text, cv_text)
            print(f"  доля заражённых={frac:.0%} (n={len(victims)}){fb_note}")
            for r in RULES:
                delta = (acc[r] - clean_acc[r]) * n
                print(f"    {r:18s} {round(acc[r] * n)}/{n} = {acc[r]:.1%}  (Δ={delta:+.1f} фото)")

    if a.none:
        merge_none, alt_none = {}, {r: {} for r in RULES if r != "merge"}
        for p in none_photos:
            i = idx_of[p]
            local_r = fs.local(i, ocr_text[p])
            answered = bool(vlm_text.get(p, "").strip())
            model_r = fs.model(i, vlm_text.get(p, "")) if answered else None
            merge_r = fs.merge(i, vlm_text.get(p, "") if answered else "", ocr_text[p])
            merge_none[p] = top1(merge_r)
            for r in RULES:
                if r == "merge":
                    continue
                alt_none[r][p] = decide(r, local_r, model_r, merge_r, answered)
        print(f"\nсмена ответа на {len(none_photos)} фото БЕЗ вина в каталоге (относительно merge):")
        for r in RULES:
            if r == "merge":
                continue
            changed = sum(1 for p in none_photos if alt_none[r][p] != merge_none[p])
            print(f"  {r:18s} сменилось {changed}/{len(none_photos)}")

    if a.dump_json:
        Path(a.dump_json).write_text(json.dumps(
            {"clean_acc": clean_acc, "clean_preds": clean_preds, "hit_local": hit_local, "hit_model": hit_model, "n": n},
            ensure_ascii=False, indent=0))
        print(f"\nдамп: {a.dump_json}")


if __name__ == "__main__":
    main()
