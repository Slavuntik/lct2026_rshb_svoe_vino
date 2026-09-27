"""Device selection and portable loading of trusted, checksum-verified TorchScript."""

import torch


def resolve_device(device):
    if device == "auto":
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        return device
    if device not in ("cuda", "cuda:0"):
        raise ValueError(
            "SHELF_DEVICE must be auto, cpu or cuda; select GPU with CUDA_VISIBLE_DEVICES"
        )
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable; use SHELF_DEVICE=cpu or auto")
    return "cuda:0"


def load_model(path, device):
    model = torch.jit.load(str(path), map_location=device).eval()
    if device != "cpu":
        return model
    # map_location remaps tensors, but CUDA traces also contain device literals
    # for arange/zeros. Freeze inlines submodules; remap those literals too.
    model = torch.jit.freeze(model)

    def remap(block):
        for node in block.nodes():
            if (
                node.kind() == "prim::Constant"
                and node.hasAttribute("value")
                and node.kindOf("value") == "s"
                and str(node.output().type()) == "Device"
                and node.s("value").startswith("cuda")
            ):
                node.s_("value", "cpu")
            for child in node.blocks():
                remap(child)

    remap(model.graph)
    return model
