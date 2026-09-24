"""Portable XFeat feature selection. Architecture/weights: verlab/accelerated_features (Apache-2.0)."""

import types
import torch
from torch import nn
from torch.nn import functional as F


def unfold_pixels(self, x, ws=8):
    return F.pixel_unshuffle(x, ws)


class PortableXFeat(nn.Module):
    def __init__(self, weights):
        super().__init__()
        from modules.model import XFeatModel

        self.net = XFeatModel().eval()
        self.net.load_state_dict(
            torch.load(weights, map_location="cpu", weights_only=True)
        )
        self.net._unfold2d = types.MethodType(unfold_pixels, self.net)

    def forward(self, image):
        descriptors, logits, reliability = self.net(image)
        descriptors = F.normalize(descriptors, dim=1)
        heat = F.pixel_shuffle(F.softmax(logits, dim=1)[:, :64], 8)
        nms = (heat == F.max_pool2d(heat, 5, stride=1, padding=2)) & (heat > 0.05)
        height, width = image.shape[2:]
        yy, xx = torch.meshgrid(
            torch.arange(height, device=image.device),
            torch.arange(width, device=image.device),
            indexing="ij",
        )
        shape = torch._shape_as_tensor(image).to(image)
        scale = torch.stack((shape[3] - 1, shape[2] - 1))
        grid = 2 * torch.stack((xx, yy), dim=-1).to(image) / scale - 1
        reliability = F.grid_sample(
            reliability, grid[None], mode="bilinear", align_corners=False
        )
        scores = heat * nms * reliability
        values, indices = scores.flatten(1).topk(512, dim=1)
        points = torch.stack((indices % width, indices // width), dim=-1).to(image)
        sampled = F.grid_sample(
            descriptors,
            (2 * points / scale - 1)[:, :, None],
            mode="bicubic",
            align_corners=False,
        )
        sampled = F.normalize(sampled[:, :, :, 0].transpose(1, 2), dim=-1)
        return points, sampled, values
