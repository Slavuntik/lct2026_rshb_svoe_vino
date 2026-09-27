import pytest

torch = pytest.importorskip("torch")
from shelf_api.runtime import load_model, resolve_device
from shelf_api.vlm import LiteLLMClient, LiteLLMEngine


def test_cpu_selection_without_cuda(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert resolve_device("auto") == resolve_device("cpu") == "cpu"
    with pytest.raises(RuntimeError):
        resolve_device("cuda")
    with pytest.raises(ValueError):
        resolve_device("cuda:2")


def test_cuda_literals_in_saved_model_remapped(tmp_path):
    class Model(torch.nn.Module):
        def forward(self, x):
            return x + torch.arange(x.size(0), device="cpu")

    model = torch.jit.trace(Model().eval(), torch.zeros(3))
    for node in model.graph.nodes():
        if str(node.output().type()) == "Device":
            node.s_("value", "cuda:0")
    path = tmp_path / "model.pt"
    model.save(str(path))
    assert torch.equal(load_model(path, "cpu")(torch.zeros(4)), torch.arange(4))


def test_model_reply_cannot_invent_or_duplicate_candidates():
    slots = {1: {"A": "wine"}, 2: {"B": "other"}}

    def item(i, slot):
        return dict(crop_id=i, selected_slot=slot, evidence="label")

    assert LiteLLMClient.validate({"bottles": [item(1, "A")]}, slots) == {0: "wine"}
    assert (
        LiteLLMClient.validate(
            {"bottles": [item(1, "A"), item(1, "A"), item(2, "wine"), item(True, "A")]},
            slots,
        )
        == {}
    )
    with pytest.raises(ValueError):
        LiteLLMClient.validate([], slots)


@pytest.mark.parametrize(
    "failure,accepted,inliers,expected",
    [
        (True, "wine", 20, None),
        (False, "other", 20, None),
        (False, "wine", 15, None),
        (False, "wine", 16, "wine"),
    ],
)
def test_refinement_preserves_native_results_and_requires_geometry(
    failure, accepted, inliers, expected
):
    class Client:
        def choose(self, *args):
            if failure:
                raise RuntimeError("provider failure")
            return {0: "wine"}

    engine = object.__new__(LiteLLMEngine)
    engine.vlm_client, engine.vlm_limit = Client(), 3
    engine.wines, engine.scan_warnings = {}, []
    engine.learned_many = lambda *args: [{"id": "wine", "labelInliers": inliers}]
    engine.resolve = lambda *args: accepted
    native = {"id": "native"}
    uncertain = {
        "id": None,
        "retrieval_q": {},
        "q": {},
        "box": [0, 0, 1, 1],
        "evidence": [],
        "ranking": ["wine", "other"],
    }
    engine.refine(None, [native, uncertain], set(), 0)
    assert native["id"] == "native"
    assert uncertain["id"] == expected
