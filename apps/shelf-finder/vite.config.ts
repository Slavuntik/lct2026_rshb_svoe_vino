import { defineConfig } from 'vite';
const headers = { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' };
const proxy = { '/v1/shelf': { target: process.env.SHELF_API_PROXY || 'http://127.0.0.1:8086', changeOrigin: true } };
export default defineConfig({ base: './', worker: { format: 'es' }, server: { host: '0.0.0.0', port: 5180, headers, proxy }, preview: { headers, proxy }, build: { target: 'es2022' } });
