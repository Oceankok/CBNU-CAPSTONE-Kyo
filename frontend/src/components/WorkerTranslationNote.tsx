import type { WorkerTranslationFields } from '../api/workerTranslation';

const STATUS = {
  missing: '이 언어의 번역이 아직 없습니다. 한국어 원문을 표시합니다.',
  pending: '번역 준비 중입니다. 한국어 원문을 먼저 볼 수 있습니다.',
  failed: '번역을 준비하지 못했습니다. 한국어 원문을 확인하거나 관리자에게 문의하세요.',
  disabled: '자동 번역이 꺼져 있어 한국어 원문을 표시합니다.',
  ineligible: '이 자료는 한국어 원문으로 제공됩니다.',
};
export default function WorkerTranslationNote({ item }: { item: WorkerTranslationFields }) {
  const meta = item.translation;
  if (!meta) return null;
  return <div>
    <p role="status">{meta.origin === 'ai' ? 'AI 자동 번역' : meta.origin === 'reviewed' ? '수동 번역' : '한국어 원문'} · {item.language ?? 'ko'}
      {item.requested_language && item.translation_fallback && ` · 요청 언어 ${item.requested_language}`}
    </p>
    {meta.status in STATUS && <p role="status">{STATUS[meta.status as keyof typeof STATUS]}</p>}
    {meta.origin !== 'ko' && item.original && <details>
      <summary>한국어 원문 보기</summary>
      <h4>{item.original.title}</h4><p style={{ whiteSpace: 'pre-wrap' }}>{item.original.body}</p>
      <ul>{item.original.checklist.map((line, index) => <li key={index}>{line}</li>)}</ul>
    </details>}
  </div>;
}
