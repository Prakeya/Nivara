import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/upload': 'http://localhost:8000',
      '/status': 'http://localhost:8000',
      '/audit': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/metrics': 'http://localhost:8000',
      '/v1': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom'],
          'charts': ['recharts'],
        },
      },
    },
  },
})
