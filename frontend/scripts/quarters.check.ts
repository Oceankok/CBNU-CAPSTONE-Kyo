// Run: npm test
import assert from 'node:assert/strict';
import { quarterOf, recentQuarters } from '../src/quarters.ts';

assert.equal(quarterOf(new Date(2026, 0, 1)), '2026-Q1');
assert.equal(quarterOf(new Date(2026, 2, 31)), '2026-Q1');
assert.equal(quarterOf(new Date(2026, 9, 6)), '2026-Q4');
assert.deepEqual(recentQuarters(4, new Date(2026, 9, 6)), ['2026-Q1', '2026-Q2', '2026-Q3', '2026-Q4']);
// Crosses a year boundary (and month-end days like the 31st)
assert.deepEqual(recentQuarters(3, new Date(2026, 1, 28)), ['2025-Q3', '2025-Q4', '2026-Q1']);
assert.deepEqual(recentQuarters(2, new Date(2026, 4, 31)), ['2026-Q1', '2026-Q2']);
console.log('quarters ok');
