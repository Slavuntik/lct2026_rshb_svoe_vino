# Third-party model components

The learned recognition pipelines (including the default hybrid mode) use:

- **ALIKED n16**, Zhao Xiaoming and collaborators, BSD-3-Clause. Source and weights: https://github.com/Shiaoming/ALIKED. Full notice: `licenses/ALIKED-BSD-3-Clause.txt`.
- **LightGlue**, CVG/ETH Zurich, Apache-2.0. Source and trained intermediate matching heads: https://github.com/cvg/LightGlue. The mobile export uses the first five pretrained layers and 256 points; it is not the unchanged nine-layer matcher.
- **LightGlue-ONNX**, Fabio Milentiansen Sim and contributors, Apache-2.0, revision `d12b4ba1632f558234e3f084e1f3d8bdf9147890`. Source: https://github.com/fabio-sim/LightGlue-ONNX. Full license: `licenses/LightGlue-ONNX-Apache-2.0.txt`. `scripts/learned_model.py` adapts its portable deformable convolution/descriptor implementation, through an explicit source checkout dependency, and ALIKED's score head and subpixel keypoint selection.
- **XFeat and LighterGlue**, Verlab, Apache-2.0, revision `e92685f57f8318b18725c5c8c0bd28c7fe188d9a`. Source: https://github.com/verlab/accelerated_features. Full license: `licenses/XFeat-Apache-2.0.txt`. The default hybrid pipeline uses the six-layer, 96-dimensional LighterGlue with 256 matching points.
- OpenCV.js, via `@techstark/opencv-js`, for descriptor matching and RANSAC. Package licenses remain in the dependency distribution.

YOLO and original MobileNet components retain the licenses listed in README. Organizer reference images remain dataset assets; preparation does not grant additional rights to redistribute them. The learned bundle contains reference features, not shelf photographs or manual review labels.
