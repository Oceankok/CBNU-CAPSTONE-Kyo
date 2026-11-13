// FastAPI can return a text detail, validation array, or structured conflict.
export function formatErrorDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (detail == null) return '요청 처리 실패';
  if (Array.isArray(detail)) return detail.map(item => {
    if (item && typeof item === 'object' && 'msg' in item) return String(item.msg);
    return formatErrorDetail(item);
  }).join(', ');
  if (typeof detail === 'object') return JSON.stringify(detail);
  return String(detail);
}
