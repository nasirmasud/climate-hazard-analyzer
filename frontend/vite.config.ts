import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],

  server: {
    // The dev port, stated explicitly so the CORS allowlist and the console
    // output agree on one number.
    port: 5173,

    // Fail loudly instead of quietly moving to another port.
    //
    // Without this, Vite falls back to 5174, 5175 and onwards when 5173 is
    // taken. The page still loads, so nothing looks broken, but a CORS allowlist
    // keyed to 5173 rejects the API calls and the UI reports "backend
    // unreachable" — while the backend is healthy and opening its URL in a tab
    // works fine, because a direct navigation sends no Origin header and CORS
    // never applies to it.
    //
    // That combination is genuinely confusing to debug: the failure looks like a
    // backend problem and is actually a port number. Exiting with a clear error
    // beats serving a page that silently cannot reach the API.
    strictPort: true,
  },
})