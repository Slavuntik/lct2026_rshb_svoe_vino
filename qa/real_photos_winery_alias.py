"""Алиасы винодельни из СТРУКТУРЫ каталога (общий редкий токен поля "Винодельня"),
БЕЗ обращения к 62 фото — и проверка эффекта на гейте подтверждения винодельни
(agents/H1-cpu-path.md) по всем источникам текста. Основание: reports/ml-lead-plan.md
(Голубицкое 93.97 — гейт не подтверждает истину, т.к. «Поместье Голубицкое» и
«Golubitskoe Estate» — два написания одного производителя в каталоге).

НАХОДКА: чисто статистический анкор (низкий df токена среди 135 уникальных строк
"Винодельня") не отличает настоящий алиас ("golubitskoe", df=2 — Поместье Голубицкое /
Golubitskoe Estate, один производитель) от родового слова международного винного
брендинга, которое при 135 строках каталога тоже редкое: "estate" (df=2: Golubitskoe
Estate И Mantra Estate — РАЗНЫЕ), "vineyards" (df=2: Litavshchuk И AYA Organic —
РАЗНЫЕ), "chateau" (df=4: 4 РАЗНЫЕ), "семейная"/semeynaya (df=2: Колесников И
Логуновы — РАЗНЫЕ; ровно служебное слово из брифа тимлида), "alma" (df=2: Alma Valley
И Villa di Alma — общее ГЕОГРАФИЧЕСКОЕ имя, не один производитель), плюс vinnyy/villa/
domaine/vino/wine/dolina/shato/vina. Поэтому анкор = низкий df (2-4 из 135) И токен
длиной >=4 И НЕ в GENERIC_WINE_WORDS (короткий родовой словарь международного винного
брендинга — не подогнан под 62 фото, выведен из ручного разбора ВСЕХ df=2-4 токенов
каталога). Даже так: кандидат "nikolaev" (df=2, А. Гордиенко & М. Николаев /
Николаев и сыновья) остаётся НЕПОДТВЕРЖДЁННЫМ — совпадающая фамилия может быть
совпадением. Правило безопасно применять только через явный, вручную подтверждённый
список пар (как families.json для near-dup), не как автоматическую рантайм-кластеризацию.

Запуск из packages/cv: .venv/bin/python ../../qa/real_photos_winery_alias.py
"""
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from cv import text_rerank as tr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import text_v2  # noqa: E402
from text_v2 import TextIndexV2, sugar_of, query_tokens as orig_query_tokens  # noqa: E402

BASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
LAB = BASE / "real-photos-labels"
F = LAB / "features"
GREEK = str.maketrans("ΛΓΠΔΦΡΚΤΗΜΟΑΕΒΖΙΝΥΧ", "ЛГПДФРКТНМОАЕВЗИНУХ")
text_v2.query_tokens = lambda t: orig_query_tokens(t) | orig_query_tokens(t.translate(GREEK))

# короткий родовой словарь международного винного брендинга (RU+EN) — см. докстринг модуля
GENERIC_WINE_WORDS = {
    "chateau", "shato", "villa", "domaine", "estate", "vineyards", "vineyard",
    "wine", "wines", "vino", "vina", "dolina", "vinodelnya", "winery", "vinnyy",
    "semeynaya", "semeynyy", "pomeste", "usadba", "hutor", "ferma", "dom", "agro",
}
MIN_DF, MAX_DF, MIN_LEN = 2, 4, 4  # анкор: df в [2,4] из уникальных строк "Винодельня", длина >= 4

cat = tr.load_catalog_text(BASE / "strapi_output0709.csv")
by_string: dict[str, list[str]] = defaultdict(list)  # сырая строка "Винодельня" -> [слаги]
for s, e in cat.items():
    if e.winery:
        by_string[e.winery].append(s)
strings = list(by_string)
str_tokens = {w: set(tr.tokenize(w)) for w in strings}

# df — по УНИКАЛЬНОЙ СТРОКЕ (не по слагу): "сколько РАЗНЫХ виноделен используют это
# слово" — то, что нужно для алиасов; df по слагу занижает вес токена целой винодельни
# (все её SKU) только потому что у неё много позиций в каталоге.
df: dict[str, int] = defaultdict(int)
for toks in str_tokens.values():
    for t in toks:
        df[t] += 1

token_to_strings: dict[str, set[str]] = defaultdict(set)
for w, toks in str_tokens.items():
    for t in toks:
        if MIN_DF <= df.get(t, 0) <= MAX_DF and len(t) >= MIN_LEN and t not in GENERIC_WINE_WORDS:
            token_to_strings[t].add(w)

parent = {w: w for w in strings}


def find(x: str) -> str:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def union(a: str, b: str) -> None:
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb


anchor_of_pair: dict[frozenset, set] = defaultdict(set)
for t, ws in token_to_strings.items():
    ws = list(ws)
    for w2 in ws[1:]:
        union(ws[0], w2)
        anchor_of_pair[frozenset((ws[0], w2))].add(t)

clusters: dict[str, list[str]] = defaultdict(list)
for w in strings:
    clusters[find(w)].append(w)
multi = {root: members for root, members in clusters.items() if len(members) > 1}

cluster_tokens: dict[str, set[str]] = {}
for members in clusters.values():
    union_toks: set[str] = set()
    for m in members:
        union_toks |= str_tokens[m]
    for m in members:
        cluster_tokens[m] = union_toks


def winery_confirm_tokens(winery_str: str) -> set[str]:
    """Токены поля Винодельня для гейта подтверждения — объединённые по кластеру алиасов,
    если строка в него попала, иначе как сейчас (свои собственные токены)."""
    if winery_str in cluster_tokens:
        return cluster_tokens[winery_str]
    return set(tr.tokenize(winery_str)) if winery_str else set()


def main() -> None:
    print(f"каталог: {len(cat)} уникальных слагов, {len(strings)} уникальных строк 'Винодельня'")
    print(f"\nкластеров с >1 строкой (df={MIN_DF}-{MAX_DF}, вне GENERIC_WINE_WORDS): {len(multi)}")
    for root, members in sorted(multi.items(), key=lambda kv: -len(kv[1])):
        anchors: set = set()
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                anchors |= anchor_of_pair.get(frozenset((members[i], members[j])), set())
        print(f"  anchors={sorted(anchors)} :: {members}")

    print("\n--- проверка: Голубицкое (93.97), Фанагория (68.81), Литавщук (91.43) ---")
    for w in ("Поместье Голубицкое", "Golubitskoe Estate", "Фанагория",
              "Литавщук. Litavshchuk vineyards & winery"):
        print(f"  {w!r} confirm_tokens={sorted(winery_confirm_tokens(w))}")

    # --- приёмка на полном размеченном наборе: гейт БЕЗ алиасов vs С алиасами ---
    photos = json.loads((F / "photos.json").read_text())
    V = np.load(F / "index_vectors_base384_d1.npy").astype(np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    vs = json.loads((F / "index_meta_base384_d1.json").read_text())["slugs"]
    slugs = sorted(set(vs))
    pos = {s: i for i, s in enumerate(slugs)}
    vsi = np.array([pos[s] for s in vs])
    Q = np.load(F / "qemb_base384.npz")

    def cvmat(keys):
        CV = np.full((len(photos), len(slugs)), -1.0, dtype=np.float32)
        for k in keys:
            q = Q[k].astype(np.float32)
            q /= np.linalg.norm(q, axis=1, keepdims=True)
            S = q @ V.T
            for i in range(len(photos)):
                t = np.full(len(slugs), -1.0, dtype=np.float32)
                np.maximum.at(t, vsi, S[i])
                CV[i] = np.maximum(CV[i], t)
        return CV

    CV2 = cvmat(("norm", "raw"))
    extra = sorted(set(cat) - set(slugs))
    all_slugs = slugs + extra
    pn, cg = {}, {}
    with (BASE / "strapi_output0709.csv").open(newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            pn.setdefault(r["Slug"], []).append(r.get("Название фото") or "")
            cg.setdefault(r["Slug"], r.get("Категория") or "")
    ex = {s: {"category": cg.get(s, ""), "sugar": sugar_of(s, pn.get(s, []), cat[s].name)} for s in all_slugs}
    sub = {s: cat[s] for s in all_slugs}
    tv_full = TextIndexV2(sub, fields=("name", "winery", "grape", "category", "sugar"), extra=ex)
    photos_set = set(photos)
    labels = {r["photo"]: r["true_slug"] for p in (LAB / "part1.csv", LAB / "part2.csv")
              for r in csv.DictReader(p.open(newline="", encoding="utf-8"))
              if r["confidence"] in ("sure", "likely") and r["photo"] in photos_set}

    class SimpleWinIndex:
        """Гейт «подтверждена винодельня» (recall поля winery) — с алиасами или без, для сравнения."""

        def __init__(self, alias: bool):
            self.slugs = all_slugs
            self.doc_tokens = []
            for s in all_slugs:
                w = sub[s].winery if s in sub else ""
                toks = winery_confirm_tokens(w) if alias else set(tr.tokenize(w or ""))
                self.doc_tokens.append({t for t in toks if len(t) >= 3})
            df_: dict[str, int] = defaultdict(int)
            for toks in self.doc_tokens:
                for t in toks:
                    df_[t] += 1
            n = len(self.slugs)
            self.idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df_.items()}

        def scores(self, ocr_text: str):
            q = text_v2.query_tokens(ocr_text)
            if not q:
                return [0.0] * len(self.slugs), None
            vocab = set().union(*self.doc_tokens) if self.doc_tokens else set()
            best = {}
            for c in vocab:
                m = max((text_v2.token_sim(t, c) for t in q), default=0.0)
                if m > 0:
                    best[c] = m
            rec = []
            for toks in self.doc_tokens:
                tot = sum(self.idf[t] for t in toks) or 1.0
                got = sum(self.idf[t] * best[t] for t in toks if t in best)
                rec.append(got / tot)
            return rec, None

    win_plain, win_alias = SimpleWinIndex(alias=False), SimpleWinIndex(alias=True)

    def load(v):
        return {json.loads(l)["photo"]: json.loads(l)["text"]
                for l in (F / f"ocr_{v}.jsonl").read_text().splitlines() if l.strip()}

    def run(variant_texts, win_index, W=0.3, alpha=0.5):
        ok = n = 0
        miss = []
        for p, t in labels.items():
            if t == "NONE":
                continue
            i = photos.index(p)
            cvv = CV2[i]
            full = np.concatenate([cvv, np.full(len(extra), float(cvv.max()) - 0.03, dtype=np.float32)])
            text = variant_texts.get(p, "")
            _, mass = tv_full.scores(text)
            wrec, _ = win_index.scores(text)
            mass = np.array(mass, dtype=np.float32)
            wrec = np.array(wrec, dtype=np.float32)
            rel = mass / mass.max() if mass.max() > 0 else mass
            finalv = full + W * rel * np.where(wrec >= 0.5, 1.0, alpha)
            top = all_slugs[int(np.argmax(finalv))]
            n += 1
            ok += top == t
            if top != t:
                miss.append((p[:6], t[:40]))
        return ok, n, miss

    variants = {
        "rapidS_union": {ph: " ".join(load(v).get(ph, "")
                                       for v in ("rapidS_640", "rapidS_960", "lab_rapidS_1280"))
                         for ph in photos},
        "gwf1024": load("gwf1024"),
        "vlm": load("vlm"),
        "human": load("human"),
        "gwf1024+vlm": {ph: load("gwf1024").get(ph, "") + " " + load("vlm").get(ph, "") for ph in photos},
    }
    print("\n--- гейт БЕЗ алиасов (plain) vs С алиасами, alpha=0.5 (как в CV_FUSION_OCR_UNCONFIRMED_W) ---")
    for name, texts in variants.items():
        ok0, n0, miss0 = run(texts, win_plain)
        ok1, n1, miss1 = run(texts, win_alias)
        print(f"{name:16s} plain={ok0}/{n0}={ok0 / n0:.1%}  alias={ok1}/{n1}={ok1 / n1:.1%}")
        s0, s1 = {m[:2] for m in miss0}, {m[:2] for m in miss1}
        fixed, broken = s0 - s1, s1 - s0
        if fixed:
            print(f"   исправлено алиасами: {sorted(fixed)}")
        if broken:
            print(f"   СЛОМАНО алиасами: {sorted(broken)}")


if __name__ == "__main__":
    main()
