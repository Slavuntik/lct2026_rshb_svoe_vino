"""Portable ALIKED, adapted from ALIKED (BSD-3) and LightGlue-ONNX (Apache-2.0).
License texts: apps/shelf-finder/licenses/. Input: RGB [0,1], height 512, width divisible by 32.
"""

# The caller adds the pinned LightGlue-ONNX checkout to sys.path.
import torch
from torch import nn
from torch.nn import functional as F
from lightglue import ALIKED
from lightglue.aliked import simple_nms
from lightglue_dynamo.models.aliked import DeformableConv2d, SparseDescriptorHead


class Portable(nn.Module):
    def __init__(self):
        super().__init__()
        self.m = ALIKED(max_num_keypoints=512).eval()
        for block in [self.m.block3, self.m.block4]:
            for name in ["conv1", "conv2"]:
                old = getattr(block, name)
                new = DeformableConv2d(
                    old.regular_conv.in_channels,
                    old.regular_conv.out_channels,
                    portable=True,
                )
                new.load_state_dict(old.state_dict())
                setattr(block, name, new)
        self.desc = SparseDescriptorHead()
        self.desc.load_state_dict(self.m.desc_head.state_dict())

    def forward(self, x):
        m = self.m
        x1 = m.block1(x)
        x2 = m.block2(m.pool2(x1))
        x3 = m.block3(m.pool4(x2))
        x4 = m.block4(m.pool4(x3))
        f = torch.cat(
            [
                m.gate(m.conv1(x1)),
                m.upsample2(m.gate(m.conv2(x2))),
                m.upsample8(m.gate(m.conv3(x3))),
                m.upsample32(m.gate(m.conv4(x4))),
            ],
            1,
        )
        scores = torch.sigmoid(m.score_head(f))
        nms = simple_nms(scores, 2)
        h, w = x.shape[2:]
        yy = torch.arange(h, device=x.device)
        xx = torch.arange(w, device=x.device)
        nms = nms * (
            (yy[:, None] >= 2)
            & (yy[:, None] < h - 2)
            & (xx[None, :] >= 2)
            & (xx[None, :] < w - 2)
        )
        values, idx = nms.flatten(1).topk(512, dim=1)
        xy = torch.stack((idx % w, idx // w), -1).float()
        offsets = torch.tensor(
            [[a, b] for b in range(-2, 3) for a in range(-2, 3)],
            device=x.device,
            dtype=x.dtype,
        )
        pos = xy[:, :, None, :] + offsets
        flat = (
            pos[..., 1].long().clamp(0, h - 1) * w + pos[..., 0].long().clamp(0, w - 1)
        ).flatten(1)
        patches = torch.gather(scores.flatten(1), 1, flat).reshape(1, 512, 25)
        weights = torch.softmax(patches / 0.1, -1)
        xy = xy + (weights[:, :, :, None] * offsets).sum(2)
        shape = torch._shape_as_tensor(x).to(x)
        scale = torch.stack((shape[3] - 1, shape[2] - 1))
        d = self.desc(F.normalize(f, p=2, dim=1), 2 * xy / scale - 1)
        return xy, d, values
