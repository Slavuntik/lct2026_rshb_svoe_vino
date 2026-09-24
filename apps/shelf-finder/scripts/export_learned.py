"""Export the two learned models; requires the pinned LightGlue-ONNX source checkout."""

import argparse
import json
from pathlib import Path
import sys
import types
import subprocess

import numpy as np
import onnxruntime as ort
import torch


def cross_attention(self, descriptors):
    # Fixed pair batching avoids a legacy ONNX reshape/flip export error.
    from lightglue_dynamo.ops import multi_head_attention

    qk, value = self.to_qk(descriptors), self.to_v(descriptors)
    index = torch.tensor([1, 0], device=qk.device)
    message = multi_head_attention(
        qk, qk.index_select(0, index), value.index_select(0, index), self.num_heads
    )
    message = self.to_out(message)
    return descriptors + self.ffn(torch.cat([descriptors, message], 2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx-source", type=Path, required=True)
    parser.add_argument("--matcher-weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--points", type=int, default=256)
    parser.add_argument("--layers", type=int, default=5)
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "-C", str(args.onnx_source), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != "d12b4ba1632f558234e3f084e1f3d8bdf9147890":
        parser.error(
            "Use the LightGlue-ONNX revision pinned in docs/learned-recognition.md"
        )
    sys.path.insert(0, str(args.onnx_source.resolve()))
    from learned_model import Portable
    from webgpu_export import replace_selu
    from lightglue_dynamo.models.lightglue import LightGlue

    args.output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)
    torch.manual_seed(2026)
    extractor = Portable().eval()
    matcher = LightGlue(
        args.matcher_weights, input_dim=128, n_layers=args.layers
    ).eval()
    for layer in matcher.transformers:
        layer.cross_attn.forward = types.MethodType(cross_attention, layer.cross_attn)
    image = torch.rand(1, 3, 512, 192)
    points = torch.rand(2, args.points, 2) * 2 - 1
    descriptors = torch.nn.functional.normalize(torch.rand(2, args.points, 128), dim=-1)
    with torch.inference_mode():
        torch.onnx.export(
            extractor,
            image,
            str(args.output / "aliked.onnx"),
            opset_version=17,
            input_names=["image"],
            output_names=["keypoints", "descriptors", "scores"],
            dynamic_axes={"image": {3: "width"}},
            dynamo=False,
        )
        selu_rewrites = replace_selu(args.output / "aliked.onnx")
        torch.onnx.export(
            matcher,
            (points, descriptors),
            str(args.output / "lightglue.onnx"),
            opset_version=17,
            input_names=["keypoints", "descriptors"],
            output_names=["matches", "scores"],
            dynamo=False,
        )
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        extraction = ort.InferenceSession(str(args.output / "aliked.onnx"), options)
        # Include a width different from export input: real bottle aspect ratios vary.
        extraction_errors = []
        for width in (128, 192, 256):
            sample = torch.rand(1, 3, 512, width)
            expected = extractor(sample)
            actual = extraction.run(None, {"image": sample.numpy()})
            ep, ed, es = [v.numpy()[0] for v in expected]
            ap, ad, actual_scores = [v[0] for v in actual]
            # TopK can reorder virtually tied scores; compare corresponding points, not row order.
            distances = np.linalg.norm(ep[:, None] - ap[None], axis=-1)
            neighbors = distances.argmin(1)
            matched = distances[np.arange(len(ep)), neighbors] < 0.01
            descriptor_error = float(
                np.max(np.abs(ed[matched] - ad[neighbors[matched]]))
            )
            if matched.mean() < 0.99 or descriptor_error > 0.005:
                raise RuntimeError(
                    f"ALIKED export parity failed: {matched.mean()}, {descriptor_error}"
                )
            extraction_errors.append(
                {
                    "width": width,
                    "pointAgreement": float(matched.mean()),
                    "descriptorMaxError": descriptor_error,
                }
            )
        matching = ort.InferenceSession(str(args.output / "lightglue.onnx"), options)
        # Identical descriptors with slightly moved coordinates create non-empty correspondences.
        descriptors[1] = descriptors[0]
        points[1] = points[0] + 0.01
        expected = matcher(points, descriptors)
        actual = matching.run(
            None, {"keypoints": points.numpy(), "descriptors": descriptors.numpy()}
        )
        if not np.array_equal(expected[0].numpy(), actual[0]) or not np.allclose(
            expected[1].numpy(), actual[1], atol=1e-4
        ):
            raise RuntimeError("LightGlue export parity failed")
    report = {
        "extractor": extraction_errors,
        "matcherMatches": len(actual[0]),
        "sourceRevision": revision,
        "runtime": ort.__version__,
        "matcherPoints": args.points,
        "matcherLayers": args.layers,
        "webgpuSeluRewrites": selu_rewrites,
    }
    (args.output / "export-validation.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
