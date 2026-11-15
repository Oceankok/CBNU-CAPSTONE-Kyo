import { createServer } from 'vite';
import react from '@vitejs/plugin-react';

// No production config, env files, backend proxy, authentication, or DB.
const server = await createServer({
  configFile: false,
  envFile: false,
  plugins: [react()],
  define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('') },
  server: { host: '127.0.0.1', port: 5187, strictPort: true },
});
await server.listen();
