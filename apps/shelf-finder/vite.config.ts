import { defineConfig } from 'vite';
const headers = { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' };
export default defineConfig({ base: './', worker: { format: 'es' }, server: { host: '0.0.0.0', port: 5180, headers }, preview: { headers }, build: { target: 'es2022' } });
