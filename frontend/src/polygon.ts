// Mirrors the server's rules for work areas (backend/work_areas/geometry.py) so mistakes are
// shown while drawing; the server still validates. Points are normalized: (0,0) top-left, (1,1) bottom-right.
export type Point = [number, number];

export const MIN_POINTS = 3;
export const MAX_POINTS = 32;

const EPS = 1e-9;

const cross = (a: Point, b: Point, c: Point) =>
  (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);

const onSegment = (a: Point, b: Point, p: Point) =>
  Math.min(a[0], b[0]) - EPS <= p[0] &&
  p[0] <= Math.max(a[0], b[0]) + EPS &&
  Math.min(a[1], b[1]) - EPS <= p[1] &&
  p[1] <= Math.max(a[1], b[1]) + EPS;

// True when segments ab and cd share any point, touching included
export function segmentsTouch(a: Point, b: Point, c: Point, d: Point): boolean {
  const d1 = cross(a, b, c);
  const d2 = cross(a, b, d);
  const d3 = cross(c, d, a);
  const d4 = cross(c, d, b);
  if (
    ((d1 > EPS && d2 < -EPS) || (d1 < -EPS && d2 > EPS)) &&
    ((d3 > EPS && d4 < -EPS) || (d3 < -EPS && d4 > EPS))
  )
    return true;
  return (
    (Math.abs(d1) <= EPS && onSegment(a, b, c)) ||
    (Math.abs(d2) <= EPS && onSegment(a, b, d)) ||
    (Math.abs(d3) <= EPS && onSegment(c, d, a)) ||
    (Math.abs(d4) <= EPS && onSegment(c, d, b))
  );
}

export function area(points: Point[]): number {
  let sum = 0;
  points.forEach((p, i) => {
    const q = points[(i + 1) % points.length];
    sum += p[0] * q[1] - q[0] * p[1];
  });
  return Math.abs(sum) / 2;
}

// Returns a Korean message for the first problem, or null when the shape can be saved
export function polygonProblem(points: Point[]): string | null {
  const n = points.length;
  if (n < MIN_POINTS) return `꼭짓점이 ${MIN_POINTS}개 이상 필요합니다.`;
  if (n > MAX_POINTS) return `꼭짓점은 ${MAX_POINTS}개까지 가능합니다.`;
  for (let i = 0; i < n; i++)
    for (let j = i + 1; j < n; j++)
      if (
        Math.abs(points[i][0] - points[j][0]) < EPS &&
        Math.abs(points[i][1] - points[j][1]) < EPS
      )
        return '같은 위치에 점이 두 번 찍혀 있습니다.';
  for (let i = 0; i < n; i++) {
    const a = points[i];
    const b = points[(i + 1) % n];
    for (let j = i + 1; j < n; j++) {
      // adjacent edges meet at a shared vertex by design
      if (j === i || (j + 1) % n === i || (i + 1) % n === j) continue;
      if (segmentsTouch(a, b, points[j], points[(j + 1) % n]))
        return '선이 서로 교차합니다. 점 위치를 조정하세요.';
    }
  }
  if (area(points) < 1e-4) return '영역이 너무 작거나 일직선입니다.';
  return null;
}

export const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
