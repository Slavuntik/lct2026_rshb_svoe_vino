"""Combine ALIKED candidate retrieval with compact XFeat/LighterGlue verification."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("aliked", "xfeat", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    from onnx import helper, TensorProto, save_model

    a = json.loads((args.aliked / "local-index.json").read_text())
    x = json.loads((args.xfeat / "local-index.json").read_text())
    if a["ids"] != x["ids"]:
        raise ValueError("The two reference galleries have different catalog orders")
    args.output.mkdir(parents=True, exist_ok=True)
    for filename in ("xfeat.onnx", "lighterglue.onnx", "detector.onnx", "catalog.json"):
        shutil.copyfile(args.xfeat / filename, args.output / filename)
    shutil.copytree(
        args.xfeat / "references", args.output / "references", dirs_exist_ok=True
    )
    for filename in ("centers.bin", "vectors.bin"):
        shutil.copyfile(args.aliked / filename, args.output / filename)
    shutil.copyfile(args.aliked / "aliked.onnx", args.output / "retriever.onnx")
    shutil.copytree(
        args.aliked / "references", args.output / "verification", dirs_exist_ok=True
    )
    x["verificationReferences"] = {
        key: filename.replace("references/", "verification/", 1)
        for key, filename in a["references"].items()
    }
    x["retrievalDimension"] = a["dimension"]
    (args.output / "local-index.json").write_text(json.dumps(x))
    catalog = json.loads((args.output / "catalog.json").read_text())
    catalog["embeddingModel"] = (
        "hybrid-aliked-xfeat-v1-"
        + hashlib.sha256((args.output / "centers.bin").read_bytes()).hexdigest()[:12]
    )
    (args.output / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False))
    manifest = json.loads((args.xfeat / "manifest.json").read_text())
    dot_graph = helper.make_graph(
        [
            helper.make_node("Transpose", ["reference"], ["transposed"], perm=[1, 0]),
            helper.make_node("MatMul", ["query", "transposed"], ["similarities"]),
        ],
        "descriptor-dot",
        [
            helper.make_tensor_value_info("query", TensorProto.FLOAT, ["n", "d"]),
            helper.make_tensor_value_info("reference", TensorProto.FLOAT, ["m", "d"]),
        ],
        [helper.make_tensor_value_info("similarities", TensorProto.FLOAT, ["n", "m"])],
    )
    dot = helper.make_model(dot_graph, opset_imports=[helper.make_opsetid("", 13)])
    dot.ir_version = 8
    save_model(dot, str(args.output / "descriptor-dot.onnx"))
    manifest["descriptorDot"] = "descriptor-dot.onnx"
    manifest.update(
        {
            "retriever": "retriever.onnx",
            "embeddingModel": catalog["embeddingModel"],
            "hashes": {},
        }
    )
    license_source = Path(__file__).resolve().parent.parent / "licenses"
    for path in license_source.glob("*.txt"):
        shutil.copyfile(path, args.output / path.name)
    for path in args.output.rglob("*"):
        if path.is_file() and path.name != "manifest.json":
            manifest["hashes"][path.relative_to(args.output).as_posix()] = (
                hashlib.sha256(path.read_bytes()).hexdigest()
            )
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f'Prepared {len(x["ids"])} wines in {args.output}')


if __name__ == "__main__":
    main()
