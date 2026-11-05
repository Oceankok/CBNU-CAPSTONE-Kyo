// Run: npm test
import assert from 'node:assert/strict';
import { polygonProblem, area } from '../src/polygon.ts';
import type { Point } from '../src/polygon.ts';

const square: Point[] = [
  [0.1, 0.1],
  [0.6, 0.1],
  [0.6, 0.6],
  [0.1, 0.6],
];
assert.equal(polygonProblem(square), null);
assert.equal(polygonProblem([...square].reverse()), null); // direction does not matter
// concave (L shape) is allowed
assert.equal(
  polygonProblem([
    [0.1, 0.1],
    [0.7, 0.1],
    [0.7, 0.4],
    [0.4, 0.4],
    [0.4, 0.8],
    [0.1, 0.8],
  ]),
  null,
);
assert.match(polygonProblem(square.slice(0, 2)) ?? '', /3개/);
// bow-tie: edges cross
assert.match(
  polygonProblem([
    [0.1, 0.1],
    [0.6, 0.6],
    [0.6, 0.1],
    [0.1, 0.6],
  ]) ?? '',
  /교차/,
);
// duplicate point
assert.match(
  polygonProblem([
    [0.1, 0.1],
    [0.6, 0.1],
    [0.6, 0.1],
    [0.1, 0.6],
  ]) ?? '',
  /두 번/,
);
// collinear
assert.match(
  polygonProblem([
    [0.1, 0.1],
    [0.3, 0.3],
    [0.6, 0.6],
  ]) ?? '',
  /일직선|작/,
);
// 33 points
const many: Point[] = Array.from({ length: 33 }, (_, i) => [
  0.5 + 0.4 * Math.cos((i / 33) * 2 * Math.PI),
  0.5 + 0.4 * Math.sin((i / 33) * 2 * Math.PI),
]);
assert.match(polygonProblem(many) ?? '', /32개/);
assert.ok(Math.abs(area(square) - 0.25) < 1e-9);
console.log('polygon ok');
