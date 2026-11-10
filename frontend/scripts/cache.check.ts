// Run: npm test
import assert from 'node:assert/strict';
import { createCache } from '../src/api/cache.ts';

let owner: string | null = 'admin';
let clock = 1000;
const c = createCache(
  () => owner,
  () => clock,
);
let calls = 0;
const fetcher =
  (v = 'v') =>
  async () =>
    `${v}${++calls}`;
const tick = () => new Promise((r) => setTimeout(r, 0));

// served from cache inside the ttl, refetched after it
assert.equal(await c.cached('k', 100, fetcher()), 'v1');
assert.equal(await c.cached('k', 100, fetcher()), 'v1');
assert.equal(calls, 1);
clock += 150;
assert.equal(await c.cached('k', 100, fetcher()), 'v2');

// ttl 0 never serves a stored value
assert.equal(await c.cached('k', 0, fetcher()), 'v3');
assert.equal(await c.cached('k', 0, fetcher()), 'v4');

// concurrent reads share one request
calls = 0;
const [a, b] = await Promise.all([
  c.cached('shared', 100, fetcher('s')),
  c.cached('shared', 100, fetcher('s')),
]);
assert.equal(a, 's1');
assert.equal(b, 's1');
assert.equal(calls, 1);

// invalidate by prefix only
await c.cached('events:a', 1000, fetcher('e'));
const detail = await c.cached('event:1', 1000, fetcher('d'));
c.invalidate('events:');
calls = 0;
assert.equal(await c.cached('events:a', 1000, fetcher('e')), 'e1'); // refetched
assert.equal(await c.cached('event:1', 1000, fetcher('d')), detail); // untouched: same value, no new call
assert.equal(calls, 1);

// a read that started before an invalidation must not store its (stale) result
let release!: (v: string) => void;
const slow = c.cached(
  'late',
  1000,
  () => new Promise<string>((r) => (release = r)),
);
c.invalidate('late');
release('old');
assert.equal(await slow, 'old'); // the original caller still gets its answer
await tick();
calls = 0;
assert.equal(await c.cached('late', 1000, fetcher('n')), 'n1'); // but it was not cached
assert.equal(calls, 1);

// errors are not cached
await assert.rejects(
  c.cached('boom', 1000, async () => {
    throw new Error('x');
  }),
);
assert.equal(await c.cached('boom', 1000, async () => 'ok'), 'ok');

// another user starts empty
await c.cached('mine', 1000, fetcher('m'));
owner = 'other';
calls = 0;
assert.equal(await c.cached('mine', 1000, fetcher('m')), 'm1');
assert.equal(calls, 1);

console.log('cache ok');
