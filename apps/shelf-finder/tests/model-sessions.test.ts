import { expect, it } from 'vitest';
import type * as ORT from 'onnxruntime-web';
import { createLearnedSessions } from '../src/model-sessions';
it('releases a GPU graph that fails during execution and retries the entire group on WASM', async () => {
  const providers: string[] = [];
  let released = 0;
  const runtime = {
    Tensor: class {
      dispose() {}
    },
    InferenceSession: {
      create: async (_bytes: ArrayBuffer, options: { executionProviders: string[] }) => {
        const provider = options.executionProviders[0];
        providers.push(provider);
        return {
          run: async () => {
            if (provider === 'webgpu') throw new Error('unsupported kernel');
            return {};
          },
          release: async () => {
            released++;
          }
        };
      }
    }
  } as unknown as typeof ORT;
  const bytes = {
    extractor: new ArrayBuffer(1),
    matcher: new ArrayBuffer(1),
    retriever: new ArrayBuffer(1)
  };
  const sessions = await createLearnedSessions(runtime, bytes, 64, true);
  expect(sessions.backend).toBe('wasm');
  expect(providers).toEqual(['webgpu', 'webgpu', 'webgpu', 'wasm', 'wasm', 'wasm']);
  expect(released).toBe(3);
});
it('does not mask a broken model when WASM initialization also fails', async () => {
  const runtime = {
    InferenceSession: {
      create: async () => {
        throw new Error('broken model');
      }
    }
  } as unknown as typeof ORT;
  await expect(
    createLearnedSessions(
      runtime,
      { extractor: new ArrayBuffer(1), matcher: new ArrayBuffer(1) },
      64,
      true
    )
  ).rejects.toThrow('broken model');
});
