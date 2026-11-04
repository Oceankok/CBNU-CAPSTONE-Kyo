// All times are shown in Korea time so the screen does not depend on the viewer's PC time zone (#198)
const ZONE = 'Asia/Seoul';

type Input = string | number | Date | null | undefined;

const toDate = (v: Input, seconds = false): Date | null => {
  if (v === null || v === undefined || v === '') return null;
  const d = new Date(seconds && typeof v === 'number' ? v * 1000 : v);
  return Number.isNaN(d.getTime()) ? null : d;
};

export function formatSeoul(
  v: Input,
  opts: Intl.DateTimeFormatOptions = {},
  seconds = false,
): string {
  const d = toDate(v, seconds);
  if (!d) return '-';
  return d.toLocaleString('ko-KR', { timeZone: ZONE, ...opts });
}

// Records saved before time zones were stored have no zone; show them as recorded, never guessed
export function formatEventTime(
  value: string,
  timezone: string | null | undefined,
  opts: Intl.DateTimeFormatOptions = {},
): string {
  if (timezone === null)
    return `${value.replace('T', ' ').slice(0, 19)} (시간대 미확인)`;
  return formatSeoul(value, opts);
}

// Quarter id of "now" by the Korea calendar
export function seoulQuarterNow(now = new Date()): string {
  const [y, m] = new Intl.DateTimeFormat('en-CA', {
    timeZone: ZONE,
    year: 'numeric',
    month: '2-digit',
  })
    .format(now)
    .split('-')
    .map(Number);
  return `${y}-Q${Math.floor((m - 1) / 3) + 1}`;
}
