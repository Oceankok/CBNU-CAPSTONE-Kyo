// Small request cache: repeated reads within a short time share one result, and a read that is
// already in flight is joined instead of sent twice. Writes call invalidate() so nothing stale is shown.
// Pure on purpose (no imports) so it can be tested without a browser.
interface Entry {
  at: number;
  value?: unknown;
  promise?: Promise<unknown>;
}

export function createCache(
  getOwner: () => string | null,
  now = () => Date.now(),
) {
  const store = new Map<string, Entry>();
  let epoch = 0; // bumped by every invalidation so an older in-flight read cannot store stale data
  let owner: string | null = getOwner();

  // A different signed-in user never sees the previous user's cached data
  const checkOwner = () => {
    const current = getOwner();
    if (current !== owner) {
      owner = current;
      store.clear();
      epoch++;
    }
  };

  function cached<T>(
    key: string,
    ttlMs: number,
    fetcher: () => Promise<T>,
  ): Promise<T> {
    checkOwner();
    const hit = store.get(key);
    if (hit?.promise) return hit.promise as Promise<T>;
    // ttlMs 0 = always read fresh (but still joins a request already in flight)
    if (hit && ttlMs > 0 && now() - hit.at < ttlMs)
      return Promise.resolve(hit.value as T);

    const startedAt = epoch;
    const promise: Promise<T> = fetcher().then(
      (value) => {
        if (startedAt === epoch && store.get(key)?.promise === promise) {
          store.set(key, { at: now(), value });
        }
        return value;
      },
      (error) => {
        // Failures are never cached
        if (store.get(key)?.promise === promise) store.delete(key);
        throw error;
      },
    );
    store.set(key, { at: now(), promise });
    return promise;
  }

  // Drop every entry whose key starts with the prefix ('' drops everything)
  function invalidate(prefix = '') {
    epoch++;
    for (const key of [...store.keys()])
      if (key.startsWith(prefix)) store.delete(key);
  }

  return { cached, invalidate, clear: () => invalidate('') };
}
