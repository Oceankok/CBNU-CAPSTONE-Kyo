// Quarter ids look like "2026-Q4"

export function quarterOf(date: Date): string {
  return `${date.getFullYear()}-Q${Math.floor(date.getMonth() / 3) + 1}`;
}

// Current quarter and the n-1 before it, oldest first (e.g. 2026-Q1 … 2026-Q4)
export function recentQuarters(n = 4, now = new Date()): string[] {
  return Array.from({ length: n }, (_, i) =>
    quarterOf(new Date(now.getFullYear(), now.getMonth() - 3 * (n - 1 - i), 1)),
  );
}
