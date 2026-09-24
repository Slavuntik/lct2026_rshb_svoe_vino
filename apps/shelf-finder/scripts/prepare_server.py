"""Build a self-contained native GPU bundle from the browser gallery; no shelf labels."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import torch


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ["browser", "onnx-source", "xfeat-source", "output"]:
        p.add_argument("--" + n, type=Path, required=True)
    args = p.parse_args()
    for path, expected in [
        (args.onnx_source, "d12b4ba1632f558234e3f084e1f3d8bdf9147890"),
        (args.xfeat_source, "e92685f57f8318b18725c5c8c0bd28c7fe188d9a"),
    ]:
        if (
            subprocess.check_output(
                ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
            ).strip()
            != expected
        ):
            p.error("Use pinned source revisions from docs/learned-recognition.md")
    if args.output.resolve() == args.browser.resolve():
        p.error("Use a separate output directory")
    sys.path[:0] = [str(args.onnx_source.resolve()), str(args.xfeat_source.resolve())]
    import lightglue_dynamo.models.aliked as aliked_ops

    aliked_ops.shape_as_tensor = torch._shape_as_tensor
    from learned_model import Portable
    from xfeat_model import PortableXFeat
    from lightglue_dynamo.models.lightglue import LightGlue

    torch.set_num_threads(2)
    torch.manual_seed(2026)
    if not torch.cuda.is_available():
        raise RuntimeError(
            "Export on the target CUDA runtime; set CUDA_VISIBLE_DEVICES to one free GPU"
        )
    args.output.mkdir(parents=True, exist_ok=True)
    original = json.loads((args.browser / "manifest.json").read_text())
    index = json.loads((args.browser / "local-index.json").read_text())
    if index.get("extractor") != "xfeat" or not index.get("verificationReferences"):
        p.error("A complete hybrid browser bundle is required")
    files = {
        "catalog.json",
        "local-index.json",
        "centers.bin",
        "vectors.bin",
        original["detector"],
        *index["references"].values(),
        *index["verificationReferences"].values(),
    }
    for name in files:
        src = (args.browser / name).resolve()
        if not src.is_relative_to(args.browser.resolve()):
            raise ValueError("Invalid bundle path")
        if hashlib.sha256(src.read_bytes()).hexdigest() != original["hashes"].get(name):
            raise ValueError("Corrupt browser asset: " + name)
        dst = args.output / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    state = torch.load(
        args.xfeat_source / "weights/xfeat-lighterglue.pt",
        map_location="cpu",
        weights_only=True,
    )
    weights = args.output / "temporary-matcher.pt"
    torch.save({k.replace("matcher.", ""): v for k, v in state.items()}, weights)
    matcher = (
        LightGlue(weights, input_dim=64, descriptor_dim=96, n_layers=6, num_heads=1)
        .eval()
        .cuda()
    )
    weights.unlink()
    report = {}
    with torch.inference_mode():
        for name, model in [
            ("retriever", Portable().eval().cuda()),
            (
                "extractor",
                PortableXFeat(args.xfeat_source / "weights/xfeat.pt").eval().cuda(),
            ),
        ]:
            sample = torch.rand(1, 3, 512, 192, device="cuda")
            traced = torch.jit.trace(model, sample, check_trace=False)
            traced.save(str(args.output / (name + ".pt")))
            loaded = torch.jit.load(str(args.output / (name + ".pt")))
            checks = []
            for width in [64, 128, 192, 256, 512]:
                sample = torch.rand(1, 3, 512, width, device="cuda")
                expected = model(sample)
                actual = loaded(sample)
                for a, b in zip(actual, expected):
                    torch.testing.assert_close(a, b, rtol=1e-4, atol=1e-5)
                checks.append(
                    {
                        "width": width,
                        "maxError": max(
                            float((a - b).abs().max()) for a, b in zip(actual, expected)
                        ),
                    }
                )
            report[name] = checks
        k = torch.rand(2, 256, 2, device="cuda") * 2 - 1
        d = torch.nn.functional.normalize(torch.rand(2, 256, 64, device="cuda"), dim=-1)
        d[1] = d[0]
        k[1] = k[0] + 0.01
        traced = torch.jit.trace(matcher, (k, d), check_trace=False)
        traced.save(str(args.output / "matcher.pt"))
        for _ in range(3):
            k = torch.rand(2, 256, 2, device="cuda") * 2 - 1
            d = torch.nn.functional.normalize(
                torch.rand(2, 256, 64, device="cuda"), dim=-1
            )
            d[1] = d[0]
            k[1] = k[0] + 0.01
            for a, b in zip(
                torch.jit.load(str(args.output / "matcher.pt"))(k, d), matcher(k, d)
            ):
                torch.testing.assert_close(a, b, rtol=1e-4, atol=1e-5)
        batch_checks = []
        for pairs in [1, 2, 5, 16]:
            kb = torch.rand(2 * pairs, 256, 2, device="cuda") * 2 - 1
            db = torch.nn.functional.normalize(
                torch.rand(2 * pairs, 256, 64, device="cuda"), dim=-1
            )
            kb[1::2] = kb[0::2] + 0.01
            db[1::2] = db[0::2]
            actual = torch.jit.load(str(args.output / "matcher.pt"))(kb, db)
            expected = matcher(kb, db)
            for a, b in zip(actual, expected):
                torch.testing.assert_close(a, b, rtol=1e-4, atol=1e-5)
            for pair in range(pairs):
                single = matcher(
                    kb[2 * pair : 2 * pair + 2], db[2 * pair : 2 * pair + 2]
                )[0]
                torch.testing.assert_close(
                    actual[0][actual[0][:, 0] == pair][:, 1:], single[:, 1:]
                )
            batch_checks.append(pairs)
        report["matcherBatches"] = batch_checks
    (args.output / "export-validation.json").write_text(json.dumps(report, indent=2))
    for path in (Path(__file__).resolve().parents[1] / "licenses").glob("*.txt"):
        shutil.copy2(path, args.output / path.name)
    manifest = {
        "version": 1,
        "torchVersion": torch.__version__,
        "device": "cuda:0",
        "catalogSize": len(index["ids"]),
        "detector": original["detector"],
        "pipelineVersion": "native-hybrid-v1",
        "hashes": {},
    }
    for path in args.output.rglob("*"):
        if path.is_file() and path.name != "server-manifest.json":
            manifest["hashes"][path.relative_to(args.output).as_posix()] = (
                hashlib.sha256(path.read_bytes()).hexdigest()
            )
    (args.output / "server-manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
