"""Equivalent float32 activations for the ORT Web 1.22 WebGPU operator set."""

from pathlib import Path

import onnx
from onnx import TensorProto, helper


def replace_selu(path: Path) -> int:
    """Replace Selu(x) with gamma * Elu(x, alpha); preserve trained weights.

    The portable ALIKED export is float32. Unlike Selu, Elu and Mul have
    WebGPU kernels in the pinned browser runtime. This does not eliminate
    other CPU partitions (for example TopK), or imply an end-to-end speedup.
    """
    model = onnx.load(path)
    if any(
        v.type.tensor_type.elem_type != TensorProto.FLOAT for v in model.graph.input
    ):
        raise ValueError("This rewrite requires float32 model inputs")
    names = {name for node in model.graph.node for name in [*node.input, *node.output]}
    names.update(value.name for value in model.graph.initializer)

    def unique(base):
        name = base
        suffix = 0
        while name in names:
            suffix += 1
            name = f"{base}_{suffix}"
        names.add(name)
        return name

    nodes = []
    count = 0
    for node in model.graph.node:
        if node.op_type != "Selu" or node.domain not in ("", "ai.onnx"):
            nodes.append(node)
            continue
        attributes = {a.name: helper.get_attribute_value(a) for a in node.attribute}
        alpha = float(attributes.get("alpha", 1.6732631921768188))
        gamma = float(attributes.get("gamma", 1.0507010221481323))
        intermediate = unique(node.output[0] + "_webgpu_elu")
        scale = unique(node.output[0] + "_webgpu_gamma")
        model.graph.initializer.append(
            helper.make_tensor(scale, TensorProto.FLOAT, [], [gamma])
        )
        nodes.extend(
            [
                helper.make_node("Elu", list(node.input), [intermediate], alpha=alpha),
                helper.make_node("Mul", [intermediate, scale], list(node.output)),
            ]
        )
        count += 1
    if count:
        del model.graph.node[:]
        model.graph.node.extend(nodes)
        onnx.checker.check_model(model)
        onnx.save(model, path)
    return count
