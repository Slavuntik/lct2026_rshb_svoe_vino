import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
import { readFileSync, readdirSync } from 'node:fs';
const headers = { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' };
const proxy = { '/v1/shelf': { target: process.env.SHELF_API_PROXY || 'http://127.0.0.1:8086', changeOrigin: true } };
export default defineConfig({ plugins: [{name:'font-licenses', generateBundle() {
  const folder = new URL('../web/src/assets/fonts/', import.meta.url);
  for (const name of readdirSync(folder).filter(n=>n.endsWith('-OFL.txt'))) {
    this.emitFile({type:'asset',fileName:`licenses/${name}`,source:readFileSync(new URL(name,folder))});
  }
}}], base: './', worker: { format: 'es' }, server: { fs: {allow:[fileURLToPath(new URL('.', import.meta.url)), fileURLToPath(new URL('../web/src', import.meta.url))]}, host: '0.0.0.0', port: 5180, headers, proxy }, preview: { headers, proxy }, build: { target: 'es2022' } });
