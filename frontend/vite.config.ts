import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // `npm run dev:mobile`: phone hits Vite, Vite forwards to the local backend (same origin, no CORS change)
    proxy: {
      '/api': 'http://localhost:8000',
      '/storage': 'http://localhost:8000',
    },
  },
})
