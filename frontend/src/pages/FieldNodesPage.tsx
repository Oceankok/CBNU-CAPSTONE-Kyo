import { useCallback, useEffect, useState } from 'react';
import {
  fetchNodes,
  registerNode,
  fetchVoices,
  refreshVoices,
  selectVoice,
  installLanguage,
  testBroadcast,
  fetchCommands,
  linkCamera,
} from '../api/nodes';
import { fetchEvents } from '../api/events';
import type { FieldCommand, FieldNode, NodeVoices } from '../types';
import styles from './FieldNodesPage.module.css';

const POLL_MS = 10_000; // server marks a node offline after 30s without heartbeat

// Backend details are English; translate the ones an admin actually hits
const ERRORS: Record<string, string> = {
  'Node ID already exists': '이미 등록된 PC ID입니다.',
  'Node offline':
    '현장 PC가 오프라인입니다. 현장 프로그램 실행 상태를 확인하세요.',
  'Select a scanned voice first': '먼저 음성을 선택해 저장하세요.',
  "Voice is not present in this node's scanned inventory":
    '이 PC에서 검색되지 않은 음성입니다. 음성 재검색 후 다시 선택하세요.',
  'Active node not found': '사용 중인 현장 PC를 찾을 수 없습니다.',
  'Camera or node not found': '카메라 또는 현장 PC를 찾을 수 없습니다.',
  'Unsupported language': '설치할 수 없는 언어입니다.',
};
const msg = (e: unknown) => {
  const m = e instanceof Error ? e.message : String(e);
  return ERRORS[m] ?? m;
};

const LANG_LABEL: Record<string, string> = {
  'ko-KR': '한국어',
  'en-US': '영어 (미국)',
  'en-GB': '영어 (영국)',
  'ja-JP': '일본어',
  'zh-CN': '중국어 (간체)',
  'de-DE': '독일어',
  'fr-FR': '프랑스어',
  'es-ES': '스페인어',
};
const langLabel = (code: string | null) =>
  code ? (LANG_LABEL[code] ?? code) : '미설정';

const KIND_LABEL: Record<string, string> = {
  broadcast: '경고 방송',
  refresh_voices: '음성 재검색',
  install_language: '언어팩 설치',
};
const STATUS_LABEL: Record<string, string> = {
  pending: '대기',
  claimed: '실행 중',
  completed: '완료',
  failed: '실패',
  skipped: '건너뜀',
  expired: '만료',
  unknown: '확인 불가',
};

function ago(unixSec: number | null): string {
  if (!unixSec) return '연결 기록 없음';
  const s = Math.max(0, Math.round(Date.now() / 1000 - unixSec));
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  return new Date(unixSec * 1000).toLocaleString('ko-KR');
}

export default function FieldNodesPage() {
  const [nodes, setNodes] = useState<FieldNode[] | null>(null);
  const [cameras, setCameras] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [form, setForm] = useState({ node_id: '', name: '' });
  const [issued, setIssued] = useState<{
    node_id: string;
    token: string;
  } | null>(null);

  const load = useCallback(
    () =>
      fetchNodes()
        .then(setNodes)
        .catch((e) => setError(msg(e))),
    [],
  );

  useEffect(() => {
    fetchNodes()
      .then(setNodes)
      .catch((e) => setError(msg(e)));
    // ponytail: no camera list API yet — camera ids come from recorded events
    fetchEvents()
      .then(({ items }) =>
        setCameras([...new Set(items.map((e) => e.camera_id))].sort()),
      )
      .catch(() => {});
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const handleRegister = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    registerNode(form.node_id.trim(), form.name.trim())
      .then((res) => {
        setIssued(res);
        setForm({ node_id: '', name: '' });
        return load();
      })
      .catch((err) => setError(msg(err)));
  };

  return (
    <div className={styles.page}>
      <p className={styles.desc}>
        경고 방송을 실제로 출력하는 현장 PC를 관리합니다. 현장 PC에서 현장
        프로그램(field_agent)이 실행 중이어야 합니다.
      </p>
      {error && <p className={styles.errorMsg}>⚠ {error}</p>}

      {issued && (
        <section className={styles.tokenCard} role="status">
          <h3 className={styles.cardTitle}>
            '{issued.node_id}' 등록 완료 — 접속 토큰
          </h3>
          <p className={styles.warn}>
            이 토큰은 지금 한 번만 표시됩니다. 현장 PC에 저장한 뒤 창을
            닫으세요.
          </p>
          <code className={styles.token}>{issued.token}</code>
          <p className={styles.hint}>현장 PC에서 실행:</p>
          <pre className={styles.code}>
            {`$env:PPE_NODE_TOKEN = '${issued.token}'\npython -m field_agent.main --server http://<서버 주소>:8000`}
          </pre>
          <div className={styles.actions}>
            <button
              className={styles.btn}
              onClick={() => navigator.clipboard?.writeText(issued.token)}
            >
              토큰 복사
            </button>
            <button
              className={styles.primaryBtn}
              onClick={() => setIssued(null)}
            >
              저장했습니다
            </button>
          </div>
        </section>
      )}

      {nodes && nodes.length === 0 && (
        <p className={styles.desc}>등록된 현장 PC가 없습니다.</p>
      )}
      {nodes && nodes.length > 0 && (
        <ul className={styles.list}>
          {nodes.map((n) => (
            <li key={n.node_id} className={styles.node}>
              <button
                className={styles.nodeHead}
                onClick={() => setOpen(open === n.node_id ? null : n.node_id)}
                aria-expanded={open === n.node_id}
              >
                <span
                  className={`${styles.dot} ${n.online ? styles.online : ''}`}
                  aria-hidden
                />
                <span className={styles.nodeMain}>
                  <span className={styles.nodeName}>{n.name}</span>
                  <span className={styles.nodeMeta}>
                    {n.node_id} ·{' '}
                    {n.online ? '연결됨' : `오프라인 (${ago(n.last_seen_at)})`}
                  </span>
                </span>
                <span className={styles.nodeLang}>{langLabel(n.language)}</span>
                <span className={styles.chevron}>
                  {open === n.node_id ? '▲' : '▼'}
                </span>
              </button>
              {open === n.node_id && (
                <NodeDetail key={n.node_id} node={n} onChange={load} />
              )}
            </li>
          ))}
        </ul>
      )}

      {nodes && nodes.length > 0 && (
        <CameraLinks cameras={cameras} nodes={nodes} />
      )}

      {nodes && (
        <form className={styles.card} onSubmit={handleRegister}>
          <h3 className={styles.cardTitle}>현장 PC 등록</h3>
          <div className={styles.formRow}>
            <input
              className={styles.input}
              placeholder="PC ID (영문·숫자·-·_)"
              pattern="[A-Za-z0-9_\-]{1,64}"
              value={form.node_id}
              onChange={(e) => setForm({ ...form, node_id: e.target.value })}
              autoCapitalize="none"
              required
            />
            <input
              className={styles.input}
              placeholder="이름 (예: 프레스 구역 방송 PC)"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
            />
            <button className={styles.primaryBtn} type="submit">
              등록
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function NodeDetail({
  node,
  onChange,
}: {
  node: FieldNode;
  onChange: () => Promise<unknown>;
}) {
  const [voices, setVoices] = useState<NodeVoices | null>(null);
  const [commands, setCommands] = useState<FieldCommand[]>([]);
  const [voiceId, setVoiceId] = useState(node.voice_id ?? '');
  const [language, setLanguage] = useState(node.language ?? 'ko-KR');
  const [installLang, setInstallLang] = useState('');
  const [message, setMessage] = useState('방송 테스트입니다.');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const reload = useCallback(
    () =>
      Promise.all([
        fetchVoices(node.node_id),
        fetchCommands(node.node_id),
      ]).then(([v, c]) => {
        setVoices(v);
        setCommands(c);
        return v;
      }),
    [node.node_id],
  );

  useEffect(() => {
    Promise.all([fetchVoices(node.node_id), fetchCommands(node.node_id)])
      .then(([v, c]) => {
        setVoices(v);
        setCommands(c);
      })
      .catch((e) => setNote({ ok: false, text: msg(e) }));
  }, [node.node_id]);

  const run = async (op: () => Promise<unknown>, done: string) => {
    setBusy(true);
    setNote(null);
    try {
      await op();
      await reload();
      await onChange();
      setNote({ ok: true, text: done });
    } catch (e) {
      setNote({ ok: false, text: msg(e) });
    } finally {
      setBusy(false);
    }
  };

  // Queue a rescan, then wait (max 30s) for the agent to report a newer inventory
  const rescan = () =>
    run(async () => {
      const before = voices?.checked_at ?? 0;
      await refreshVoices(node.node_id);
      for (let i = 0; i < 15; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        if (((await fetchVoices(node.node_id)).checked_at ?? 0) > before)
          return;
      }
      throw new Error(
        '현장 PC가 응답하지 않습니다. 현장 프로그램 실행 상태를 확인하세요.',
      );
    }, '음성 목록을 새로 불러왔습니다.');

  const selected = voices?.items.find((v) => v.id === voiceId);

  return (
    <div className={styles.detail}>
      {note && (
        <p className={note.ok ? styles.okMsg : styles.errorMsg}>{note.text}</p>
      )}

      <section className={styles.block}>
        <h4 className={styles.blockTitle}>방송 음성</h4>
        {!voices ? (
          <p className={styles.hint}>불러오는 중...</p>
        ) : voices.items.length === 0 ? (
          <p className={styles.hint}>
            검색된 음성이 없습니다. 현장 PC가 연결된 상태에서 음성 재검색을
            실행하세요.
          </p>
        ) : (
          <div className={styles.formRow}>
            <select
              className={styles.input}
              value={voiceId}
              onChange={(e) => setVoiceId(e.target.value)}
            >
              <option value="">음성 선택</option>
              {voices.items.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                  {v.languages.length ? ` (${v.languages.join(', ')})` : ''}
                </option>
              ))}
            </select>
            <select
              className={styles.input}
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
            >
              {voices.installable_languages.map((l) => (
                <option key={l} value={l}>
                  {langLabel(l)}
                </option>
              ))}
            </select>
            <button
              className={styles.primaryBtn}
              disabled={busy || !voiceId}
              onClick={() =>
                run(
                  () => selectVoice(node.node_id, language, voiceId),
                  '방송 음성을 저장했습니다.',
                )
              }
            >
              저장
            </button>
          </div>
        )}
        <p className={styles.hint}>
          현재:{' '}
          {node.voice_id
            ? `${selected?.name ?? node.voice_id} · ${langLabel(node.language)}`
            : '미설정'}
          {voices?.checked_at && ` · 음성 목록 ${ago(voices.checked_at)} 갱신`}
        </p>
        <button
          className={styles.btn}
          disabled={busy || !node.online}
          onClick={rescan}
        >
          {busy ? '처리 중…' : '음성 재검색'}
        </button>
      </section>

      <section className={styles.block}>
        <h4 className={styles.blockTitle}>테스트 방송</h4>
        <div className={styles.formRow}>
          <input
            className={styles.input}
            value={message}
            maxLength={500}
            onChange={(e) => setMessage(e.target.value)}
          />
          <button
            className={styles.primaryBtn}
            disabled={busy || !node.online || !node.voice_id || !message.trim()}
            onClick={() =>
              run(
                () => testBroadcast(node.node_id, message.trim()),
                '테스트 방송을 보냈습니다.',
              )
            }
          >
            ▶ 방송
          </button>
        </div>
        {!node.online && (
          <p className={styles.hint}>오프라인 상태에서는 방송할 수 없습니다.</p>
        )}
      </section>

      {voices && (
        <details className={styles.block}>
          <summary className={styles.blockTitle}>언어팩 설치 (Windows)</summary>
          <p className={styles.hint}>
            현장 PC를 <code>--allow-language-install</code> 옵션과 관리자
            권한으로 실행한 경우에만 설치됩니다. 설치 후 음성 재검색을
            실행하세요.
          </p>
          <div className={styles.formRow}>
            <select
              className={styles.input}
              value={installLang}
              onChange={(e) => setInstallLang(e.target.value)}
            >
              <option value="">언어 선택</option>
              {voices.installable_languages.map((l) => (
                <option key={l} value={l}>
                  {langLabel(l)}
                </option>
              ))}
            </select>
            <button
              className={styles.btn}
              disabled={busy || !node.online || !installLang}
              onClick={() =>
                run(
                  () => installLanguage(node.node_id, installLang),
                  '설치를 요청했습니다. 아래 최근 명령에서 결과를 확인하세요.',
                )
              }
            >
              설치 요청
            </button>
          </div>
        </details>
      )}

      {commands.length > 0 && (
        <details className={styles.block}>
          <summary className={styles.blockTitle}>
            최근 명령 ({Math.min(commands.length, 10)})
          </summary>
          <ul className={styles.commands}>
            {commands.slice(0, 10).map((c) => (
              <li key={c.command_id}>
                <span>{KIND_LABEL[c.kind] ?? c.kind}</span>
                <span className={`${styles.status} ${styles[c.status] ?? ''}`}>
                  {STATUS_LABEL[c.status] ?? c.status}
                </span>
                <span className={styles.hint}>{ago(c.created_at)}</span>
                {typeof c.result?.reason === 'string' && (
                  <span className={styles.hint}>· {c.result.reason}</span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function CameraLinks({
  cameras,
  nodes,
}: {
  cameras: string[];
  nodes: FieldNode[];
}) {
  const [camera, setCamera] = useState('');
  const [output, setOutput] = useState('');
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const save = () => {
    setNote(null);
    // ponytail: source = output until camera streaming from a separate PC exists
    linkCamera(camera, output, output)
      .then(() =>
        setNote({
          ok: true,
          text: `${camera} 경고 방송은 '${nodes.find((n) => n.node_id === output)?.name}'에서 출력됩니다.`,
        }),
      )
      .catch((e) => setNote({ ok: false, text: msg(e) }));
  };

  return (
    <section className={styles.card}>
      <h3 className={styles.cardTitle}>카메라 → 방송 PC 연결</h3>
      <p className={styles.hint}>
        카메라에서 미착용이 감지되면 연결된 PC에서 경고 방송이 나옵니다.
      </p>
      {note && (
        <p className={note.ok ? styles.okMsg : styles.errorMsg}>{note.text}</p>
      )}
      <div className={styles.formRow}>
        <select
          className={styles.input}
          value={camera}
          onChange={(e) => setCamera(e.target.value)}
        >
          <option value="">카메라 선택</option>
          {cameras.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select
          className={styles.input}
          value={output}
          onChange={(e) => setOutput(e.target.value)}
        >
          <option value="">방송 PC 선택</option>
          {nodes.map((n) => (
            <option key={n.node_id} value={n.node_id}>
              {n.name}
            </option>
          ))}
        </select>
        <button
          className={styles.primaryBtn}
          disabled={!camera || !output}
          onClick={save}
        >
          연결
        </button>
      </div>
    </section>
  );
}
