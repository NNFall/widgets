import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  build: {
    // Production CSP permits fonts from this origin only. Keep even the small
    // Cyrillic-ext subsets as hashed assets instead of data: URLs in CSS.
    assetsInlineLimit: 0,
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
      '/builder': {
        target: 'http://127.0.0.1:8091',
        changeOrigin: true,
      },
    },
  },
  test: {
    css: true,
    environment: 'jsdom',
    setupFiles: './vitest.setup.ts',
  },
});
