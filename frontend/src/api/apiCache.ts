import { getSession } from './auth';
import { createCache } from './cache';

// One shared cache for the app, tied to the signed-in user
export const {
  cached,
  invalidate,
  clear: clearApiCache,
} = createCache(() => getSession()?.user_id ?? null);
