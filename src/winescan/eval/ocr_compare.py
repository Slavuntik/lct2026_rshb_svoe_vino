"""Сравнение распознавателей этикетки: EasyOCR (в сервисе) против PP-OCRv5 с кириллической моделью.

Пункт 2.7 плана закрыт отрицательным результатом с оговоркой «PP-OCRv5 не пробовался»: текстовый
канал проигрывал из-за качества OCR, а не из-за самой идеи. Модуль ставит этот опыт: берёт готовый
офлайн-прогон (там уже есть рамка, визуальный список кандидатов и текст EasyOCR), перечитывает те же
вырезки вторым движком и меряет обе роли текста — как генератора кандидатов и как добавки к
визуальному скору.

PP-OCRv5 подключается через RapidOCR (onnxruntime): PaddlePaddle требует AVX2, которого нет на
машине проекта. Зависимость необязательная и в сервис не входит — при её отсутствии модуль говорит,
что поставить. Веса: ``PaddlePaddle/cyrillic_PP-OCRv5_mobile_rec_onnx`` и
``PaddlePaddle/PP-OCRv5_mobile_det_onnx`` (Apache-2.0).

Запуск::

    python -m winescan.eval.ocr_compare --run artifacts/eval/<офлайн-прогон> \
        --split artifacts/validation/synth_v1 --limit 1000
"""

from __future__ import annotations

import argparse
import logging
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image
from scipy.stats import binomtest
from sklearn.feature_extraction.text import TfidfVectorizer

from winescan.config import project_root
from winescan.search.text_match import STOP_TOKENS, LabelText, text_score, tokens
from winescan.service.pipeline import load_cards

LOGGER = logging.getLogger(__name__)
REC_REPO = "PaddlePaddle/cyrillic_PP-OCRv5_mobile_rec_onnx"
DET_REPO = "PaddlePaddle/PP-OCRv5_mobile_det_onnx"
WEIGHTS = (0.01, 0.02, 0.03, 0.05, 0.1)

_engine = None
_images: Path | None = None


def _keys_file(characters: list[str], where: Path) -> Path:
    """Словарь символов PaddleX лежит в inference.yml, а RapidOCR ждёт его отдельным файлом."""
    path = where / "cyrillic_keys.txt"
    path.write_text("\n".join(characters) + "\n", encoding="utf-8")
    return path


def prepare_engine(cache: Path) -> tuple[str, str, str]:
    try:
        import yaml
        from huggingface_hub import snapshot_download
    except ImportError as error:  # pragma: no cover - зависит от окружения
        raise SystemExit(f"нужны huggingface_hub и pyyaml: {error}")

    rec_dir, det_dir = Path(snapshot_download(REC_REPO)), Path(snapshot_download(DET_REPO))
    characters = yaml.safe_load((rec_dir / "inference.yml").read_text())["PostProcess"]["character_dict"]
    return str(det_dir / "inference.onnx"), str(rec_dir / "inference.onnx"), str(_keys_file(characters, cache))


def read_crop(task: tuple[str, str, str, tuple[str, str, str]]) -> tuple[str, str]:
    """Читает вырезку вторым движком. Движок создаётся один раз на процесс — он весит секунды."""
    global _engine
    query_id, image_path, box, models = task
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as error:  # pragma: no cover - зависит от окружения
            raise SystemExit(f"поставьте rapidocr-onnxruntime и onnxruntime: {error}")
        det, rec, keys = models
        _engine = RapidOCR(det_model_path=det, rec_model_path=rec, rec_keys_path=keys)
    crop = Image.open(Path(image_path)).convert("RGB").crop(tuple(int(v) for v in box.split(",")))
    result, _ = _engine(np.asarray(crop))
    return query_id, " ".join(item[1] for item in (result or []))


def card_text(card: dict) -> str:
    attributes = card.get("attributes") or {}
    parts = [card.get("name", ""), card.get("winery", ""), " ".join(card.get("grapes", []) or []),
             str(attributes.get("year") or ""), card.get("region", "") or ""]  # fmt: skip
    return " ".join(tokens(" ".join(parts)))


def name_recall(texts: list[str], expected: list[str], cards: dict[str, dict]) -> float:
    """Доля значимых слов названия вина, реально прочитанных с этикетки. Честнее длины строки."""
    shares = []
    for text, slug in zip(texts, expected):
        wanted = [t for t in tokens(cards[slug]["name"]) if t not in STOP_TOKENS and not t.isdigit()]
        found = set(tokens(str(text)))
        if wanted:
            shares.append(float(np.mean([t in found for t in wanted])))
    return float(np.mean(shares)) if shares else float("nan")


def evaluate(texts: list[str], frame: pd.DataFrame, cards: dict[str, dict],
             vectorizer: TfidfVectorizer, matrix, slugs: list[str]) -> dict:  # fmt: skip
    expected = frame["expected_slug"].tolist()
    lists = [str(v).split(";") for v in frame["top_slugs"]]
    visual = [[float(x) for x in str(v).split(";")] for v in frame["top_scores"]]

    scores = (vectorizer.transform([" ".join(tokens(str(t))) for t in texts]) @ matrix.T).toarray()
    text_lists = [[slugs[i] for i in row] for row in np.argsort(-scores, axis=1)[:, :10]]
    labels = [LabelText.from_ocr(str(t)) for t in texts]

    reranked = {}
    for weight in WEIGHTS:
        reranked[weight] = np.array([
            candidates[int(np.argmax([v + weight * (text_score(cards[s], label) if s in cards else 0.0)
                                      for s, v in zip(candidates, scores_)]))] == want
            for candidates, scores_, label, want in zip(lists, visual, labels, expected)])  # fmt: skip

    return {
        "длина": float(np.mean([len(str(t)) for t in texts])),
        "пустых": float(np.mean([len(str(t)) == 0 for t in texts])),
        "полнота слов названия": name_recall(texts, expected, cards),
        "текст top-1": np.array([lst[0] == want for lst, want in zip(text_lists, expected)]),
        "текст top-10": np.array([want in lst for lst, want in zip(text_lists, expected)]),
        "спасено": sum(want not in vis and want in txt for vis, txt, want in zip(lists, text_lists, expected)),
        "переранжирование": reranked,
    }


def compare(better: np.ndarray, worse: np.ndarray) -> tuple[int, int, float]:
    fixed, broken = int((better & ~worse).sum()), int((worse & ~better).sum())
    p = binomtest(fixed, fixed + broken, 0.5).pvalue if fixed + broken else 1.0
    return fixed, broken, float(p)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="каталог офлайн-прогона с predictions.csv и ocr_text")
    parser.add_argument("--split", required=True, help="каталог выборки: queries.tsv и images/")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=6, help="потоки ONNX гасятся OMP_NUM_THREADS=1")
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = project_root(Path(__file__).resolve().parents[3], Path.cwd())
    cards = load_cards(root / "artifacts" / "catalog" / "catalog.jsonl")
    slugs = sorted(cards)
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4))
    matrix = vectorizer.fit_transform([card_text(cards[s]) for s in slugs])

    frame = pd.read_csv(Path(args.run) / "predictions.csv")
    frame = frame[frame["box"].apply(lambda v: isinstance(v, str))]
    frame = frame.sample(n=min(args.limit, len(frame)), random_state=args.seed).reset_index(drop=True)

    models = prepare_engine(Path(args.run))
    split = Path(args.split)
    images = pd.read_csv(split / "queries.tsv", sep="\t").set_index("query_id")["image_path"]
    frame = frame[frame["query_id"].isin(images.index)].reset_index(drop=True)
    tasks = [(row.query_id, str(split / "images" / images[row.query_id]), row.box, models)
             for row in frame.itertuples()]  # fmt: skip
    if not tasks:
        raise SystemExit(f"в {split} нет кадров с идентификаторами запросов из прогона")

    LOGGER.info("читаю %d вырезок вторым движком", len(tasks))
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        second = dict(pool.map(read_crop, tasks, chunksize=8))

    frame = frame[frame["query_id"].isin(second)].reset_index(drop=True)
    baseline = np.array([str(v).split(";")[0] == want for v, want in zip(frame["top_slugs"], frame["expected_slug"])])
    LOGGER.info("запросов %d, чистый визуальный порядок top-1 %.3f", len(frame), baseline.mean())

    results = {}
    for name, texts in (("EasyOCR", frame["ocr_text"].fillna("").tolist()),
                        ("PP-OCRv5", [second[q] for q in frame["query_id"]])):  # fmt: skip
        results[name] = evaluate(texts, frame, cards, vectorizer, matrix, slugs)
        row = results[name]
        print(f"\n{name}: символов {row['длина']:.1f}, пустых {row['пустых']:.1%}, "
              f"полнота слов названия {row['полнота слов названия']:.3f}")
        print(f"  только текст: top-1 {row['текст top-1'].mean():.3f}, "
              f"верное в top-10 {row['текст top-10'].mean():.3f}, спасено {row['спасено']}")
        for weight, hits in row["переранжирование"].items():
            fixed, broken, p = compare(hits, baseline)
            print(f"  вес {weight:<5}: top-1 {hits.mean():.3f} ({hits.mean() - baseline.mean():+.3f}), "
                  f"исправлено {fixed}, испорчено {broken}, p = {p:.3f}")

    print("\nдвижки между собой:")
    for key in ("текст top-1", "текст top-10"):
        fixed, broken, p = compare(results["PP-OCRv5"][key], results["EasyOCR"][key])
        print(f"  {key}: PP-OCRv5 лучше в {fixed}, хуже в {broken}, p = {p:.4f}")


if __name__ == "__main__":
    main()
