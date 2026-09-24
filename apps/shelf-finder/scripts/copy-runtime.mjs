import { mkdir, readdir, copyFile, unlink } from 'node:fs/promises';
const source = new URL('../node_modules/onnxruntime-web/dist/', import.meta.url);
const target = new URL('../public/runtime/', import.meta.url);
await mkdir(target, { recursive: true });
const files = ['ort-wasm-simd-threaded.wasm', 'ort-wasm-simd-threaded.mjs'];
for (const file of await readdir(target)) if (!files.includes(file)) await unlink(new URL(file, target));
for (const file of files) await copyFile(new URL(file, source), new URL(file, target));
