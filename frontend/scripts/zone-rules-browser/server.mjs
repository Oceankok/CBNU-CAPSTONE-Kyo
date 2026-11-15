import { createServer } from 'vite';
import react from '@vitejs/plugin-react';

// Isolated component fixture: no env files, production proxy, or backend.
const server = await createServer({
  configFile: false,
  envFile: false,
  plugins: [react()],
  define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('') },
  server: {
    host: '127.0.0.1',
    port: Number(process.env.ZONE_RULES_TEST_PORT || 5189),
    strictPort: true,
  },
});
await server.listen();
