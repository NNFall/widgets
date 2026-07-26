import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
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
