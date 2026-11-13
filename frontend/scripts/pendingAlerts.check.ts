// Run: npm test
import assert from 'node:assert/strict';
import { countNew, snapshot } from '../src/pendingAlerts.ts';

const at = (min: number) =>
  new Date(Date.UTC(2026, 10, 1, 0, min)).toISOString();
const ev = (id: string, min: number) => ({
  event_id: id,
  timestamp_start: at(min),
});

// 65 unreviewed events exist; the sample shows the newest 5 (limit 5): e65..e61
const all = Array.from({ length: 65 }, (_, i) => ev(`e${i + 1}`, i + 1)); // e1 oldest
const newest = (list: typeof all, n: number) =>
  [...list]
    .sort((a, b) => b.timestamp_start.localeCompare(a.timestamp_start))
    .slice(0, n);
const first = snapshot(newest(all, 5), 5);

// same sample again: nothing new
assert.equal(countNew(first, newest(all, 5)), 0);

// e63 is reviewed -> the older e60 slides into the window: NOT new (the #240 case)
const afterReview = newest(
  all.filter((e) => e.event_id !== 'e63'),
  5,
);
assert.ok(afterReview.some((e) => e.event_id === 'e60'));
assert.equal(countNew(first, afterReview), 0);

// a really new event arrives: counted once
const withNew = newest([...all, ev('e66', 66)], 5);
assert.equal(countNew(first, withNew), 1);
// review + new arrival in the same interval: only the new one counts
assert.equal(
  countNew(
    first,
    newest([...all.filter((e) => e.event_id !== 'e63'), ev('e66', 66)], 5),
  ),
  1,
);

// small backlog (sample not full): every unknown id is new, even with an older capture time
const small = snapshot([ev('a', 10), ev('b', 20)], 5);
assert.equal(countNew(small, [ev('a', 10), ev('b', 20), ev('late', 5)]), 1);

// a new event captured slightly before the newest one (another camera) still counts
assert.equal(countNew(first, newest([...all, ev('other-cam', 64.5)], 5)), 1);

// unparseable time with a full window: not announced
assert.equal(
  countNew(first, [{ event_id: 'x', timestamp_start: 'not a date' }]),
  0,
);

// the remembered state is only the last sample
assert.equal(snapshot(newest(all, 5), 5).ids.size, 5);
console.log('pendingAlerts ok');
