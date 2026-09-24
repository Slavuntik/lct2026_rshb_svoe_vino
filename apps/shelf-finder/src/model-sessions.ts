import type * as ORT from 'onnxruntime-web';

export async function createLearnedSessions(
  ort: typeof ORT,
  bytes: { extractor: ArrayBuffer; matcher: ArrayBuffer; retriever?: ArrayBuffer },
  descriptorDimension: number,
  preferGpu: boolean
) {
  async function create(gpu: boolean) {
    const sessions: ORT.InferenceSession[] = [];
    try {
      const options = { executionProviders: gpu ? ['webgpu', 'wasm'] : ['wasm'] };
      const extractor = await ort.InferenceSession.create(bytes.extractor, options);
      sessions.push(extractor);
      const matcher = await ort.InferenceSession.create(bytes.matcher, options);
      sessions.push(matcher);
      const retriever = bytes.retriever
        ? await ort.InferenceSession.create(bytes.retriever, options)
        : undefined;
      if (retriever) sessions.push(retriever);
      if (gpu) {
        // Some GPU kernels fail on first execution, after session creation succeeds.
        const image = new ort.Tensor('float32', new Float32Array(3 * 512 * 128), [1, 3, 512, 128]);
        const keypoints = new ort.Tensor('float32', new Float32Array(2 * 256 * 2), [2, 256, 2]);
        const descriptors = new ort.Tensor(
          'float32',
          new Float32Array(2 * 256 * descriptorDimension),
          [2, 256, descriptorDimension]
        );
        try {
          for (const session of [extractor, retriever].filter(Boolean) as ORT.InferenceSession[]) {
            const output = await session.run({ image });
            Object.values(output).forEach((t) => t.dispose());
          }
          const output = await matcher.run({ keypoints, descriptors });
          Object.values(output).forEach((t) => t.dispose());
        } finally {
          image.dispose();
          keypoints.dispose();
          descriptors.dispose();
        }
      }
      return { extractor, matcher, retriever, backend: gpu ? 'webgpu' : 'wasm' };
    } catch (error) {
      await Promise.allSettled(sessions.map((s) => s.release()));
      throw error;
    }
  }
  if (preferGpu) {
    try {
      return await create(true);
    } catch {
      /* Release GPU resources before the compatible retry. */
    }
  }
  return create(false);
}
