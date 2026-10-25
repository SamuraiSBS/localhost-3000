import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  base: '/assets/interface/',
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: {
    outDir: '../web/assets/interface', emptyOutDir: true,
    rollupOptions: {
      input: 'src/product.tsx',
      output: { entryFileNames: 'product.js', assetFileNames: asset => asset.names?.some(n => n.endsWith('.css')) ? 'product.css' : '[name]-[hash][extname]' },
    },
  },
});
