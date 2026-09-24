"""Export compact XFeat + LighterGlue and verify ONNX outputs before deployment."""

import argparse
import json
from pathlib import Path
import sys
import types
import subprocess

import numpy as np
import onnxruntime as ort
import torch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xfeat-source", "onnx-source", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    revisions = {}
    for name, path, expected in [
        ("xfeat", args.xfeat_source, "e92685f57f8318b18725c5c8c0bd28c7fe188d9a"),
        ("onnx", args.onnx_source, "d12b4ba1632f558234e3f084e1f3d8bdf9147890"),
    ]:
        revisions[name] = subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
        ).strip()
        if revisions[name] != expected:
            parser.error(
                f"Use the {name} revision pinned in docs/learned-recognition.md"
            )
    sys.path[:0] = [str(args.xfeat_source.resolve()), str(args.onnx_source.resolve())]
    from xfeat_model import PortableXFeat
    from export_learned import cross_attention
    from lightglue_dynamo.models.lightglue import LightGlue

    torch.set_num_threads(2)
    torch.manual_seed(2026)
    args.output.mkdir(parents=True, exist_ok=True)
    model = PortableXFeat(args.xfeat_source / "weights/xfeat.pt").eval()
    state = torch.load(
        args.xfeat_source / "weights/xfeat-lighterglue.pt",
        map_location="cpu",
        weights_only=True,
    )
    state = {k.replace("matcher.", ""): v for k, v in state.items()}
    checkpoint = args.output / "lighterglue-weights.pt"
    torch.save(state, checkpoint)
    matcher = LightGlue(
        checkpoint, input_dim=64, descriptor_dim=96, n_layers=6, num_heads=1
    ).eval()
    for layer in matcher.transformers:
        layer.cross_attn.forward = types.MethodType(cross_attention, layer.cross_attn)
    image = torch.rand(1, 3, 512, 192)
    points = torch.rand(2, 256, 2) * 2 - 1
    descriptors = torch.nn.functional.normalize(torch.rand(2, 256, 64), dim=-1)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    with torch.inference_mode():
        torch.onnx.export(
            model,
            image,
            str(args.output / "xfeat.onnx"),
            opset_version=17,
            input_names=["image"],
            output_names=["keypoints", "descriptors", "scores"],
            dynamic_axes={"image": {3: "width"}},
            dynamo=False,
        )
        torch.onnx.export(
            matcher,
            (points, descriptors),
            str(args.output / "lighterglue.onnx"),
            opset_version=17,
            input_names=["keypoints", "descriptors"],
            output_names=["matches", "scores"],
            dynamo=False,
        )
        session = ort.InferenceSession(str(args.output / "xfeat.onnx"), options)
        checks = []
        for width in (128, 192, 256):
            sample = torch.rand(1, 3, 512, width)
            expected = [x.numpy()[0] for x in model(sample)]
            actual = [x[0] for x in session.run(None, {"image": sample.numpy()})]
            distances = np.linalg.norm(expected[0][:, None] - actual[0][None], axis=-1)
            nearest = distances.argmin(1)
            same = distances[np.arange(512), nearest] < 0.01
            error = float(np.max(np.abs(expected[1][same] - actual[1][nearest[same]])))
            if same.mean() < 0.99 or error > 0.005:
                raise RuntimeError(
                    f"XFeat parity failed at width {width}: {same.mean()}, {error}"
                )
            checks.append(
                {
                    "width": width,
                    "pointAgreement": float(same.mean()),
                    "descriptorMaxError": error,
                }
            )
        descriptors[1] = descriptors[0]
        points[1] = points[0] + 0.01
        expected = matcher(points, descriptors)
        session = ort.InferenceSession(str(args.output / "lighterglue.onnx"), options)
        actual = session.run(
            None, {"keypoints": points.numpy(), "descriptors": descriptors.numpy()}
        )
        if not np.array_equal(expected[0].numpy(), actual[0]) or not np.allclose(
            expected[1].numpy(), actual[1], atol=1e-4
        ):
            raise RuntimeError("LighterGlue parity failed")
    checkpoint.unlink()  # Training checkpoint is not part of the browser distribution.
    report = {
        "extractor": checks,
        "matcherMatches": len(actual[0]),
        "matcherPoints": 256,
        "matcherLayers": 6,
        "xfeatRevision": revisions["xfeat"],
        "onnxSourceRevision": revisions["onnx"],
        "runtime": ort.__version__,
    }
    (args.output / "export-validation.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
