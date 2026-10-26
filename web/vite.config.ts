import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Developer and preview pages expose the same native action UI as the
// packaged server. Loopback binding alone cannot stop iframe clickjacking.
const frameHeaders = {
  'X-Frame-Options': 'DENY',
  'Content-Security-Policy': "frame-ancestors 'none'",
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    headers: frameHeaders,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  preview: { host: '127.0.0.1', headers: frameHeaders },
  build: { sourcemap: false, emptyOutDir: true },
});
