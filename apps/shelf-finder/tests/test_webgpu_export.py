"""Export rewrite checks; no model downloads or GPU required."""

import importlib.util
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import pytest
from onnx import TensorProto, helper

spec = importlib.util.spec_from_file_location(
    "webgpu_export", Path(__file__).resolve().parents[1] / "scripts/webgpu_export.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("attributes", [{}, {"alpha": 1.2, "gamma": 0.8}])
def test_selu_rewrite_preserves_values_and_is_idempotent(tmp_path, attributes):
    graph = helper.make_graph(
        [helper.make_node("Selu", ["x"], ["y"], **attributes)],
        "activation",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [9])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [9])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    path = tmp_path / "activation.onnx"
    onnx.save(model, path)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    x = np.array([-50, -3, -1, -1e-5, 0, 1e-5, 1, 3, 50], dtype=np.float32)
    expected = ort.InferenceSession(str(path), options).run(None, {"x": x})[0]
    assert module.replace_selu(path) == 1
    actual = ort.InferenceSession(str(path), options).run(None, {"x": x})[0]
    np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-6)
    assert [node.op_type for node in onnx.load(path).graph.node] == ["Elu", "Mul"]
    assert module.replace_selu(path) == 0
