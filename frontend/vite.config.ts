import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Port 5173 is not incidental: it is what the API allows by default. See
// DEFAULT_CORS_ORIGINS in backend/app/rail.py, and override both together with
// TRUSTRAIL_CORS_ORIGINS if you move it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173 },
})
