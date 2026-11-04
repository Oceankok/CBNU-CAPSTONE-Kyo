import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { MOCK_AUTH } from '../api/auth';
import { fetchCameras } from '../api/cameras';
import type { Camera } from '../api/cameras';
import { fetchCameraEquipment, fetchEquipment } from '../api/equipment';
import type { Equipment } from '../api/equipment';
import { fetchZones } from '../api/zones';
import {
  AREA_ERRORS,
  createWorkArea,
  describeIssue,
  deleteWorkArea,
  fetchSnapshot,
  fetchSnapshotImage,
  fetchWorkAreaPolicy,
  fetchWorkAreas,
  requestSnapshot,
  setWorkAreaMode,
  updateWorkArea,
} from '../api/workAreas';
import type {
  Snapshot,
  WorkArea,
  WorkAreaList,
  WorkAreaPolicy,
} from '../api/workAreas';
import ConfirmButton from '../components/ConfirmButton';
import PolygonEditor from '../components/PolygonEditor';
import PpeChips from '../components/PpeChips';
import { ZONE_PPE } from '../ppe';
import { MAX_POINTS, polygonProblem } from '../polygon';
import type { Point } from '../polygon';
import type { ZonePpe } from '../types';
import styles from './WorkAreasPage.module.css';

const SNAPSHOT_STATE: Record<Snapshot['status'], string> = {
  queued: '서버가 처리할 차례를 기다리는 중입니다…',
  running: '얼굴을 가리는 중입니다…',
  ready: '',
  failed: '기준 화면을 만들지 못했습니다.',
  expired: '기준 화면이 만료되었습니다. 다시 가져오세요.',
};

// Same rule as the server: zone baseline plus the equipment's extra PPE
const combine = (zone: ZonePpe[], extra: ZonePpe[]): ZonePpe[] =>
  ZONE_PPE.map((p) => p.value).filter(
    (v) => zone.includes(v) || extra.includes(v),
  );

interface Draft {
  id: string | 'new';
  version: number;
  name: string;
  equipmentId: string;
  points: Point[];
}

const errorText = (e: Error) => AREA_ERRORS[e.message] ?? e.message;

export default function WorkAreasPage() {
  const { camera_id = '' } = useParams<{ camera_id: string }>();
  const [camera, setCamera] = useState<Camera | null>(null);
  const [list, setList] = useState<WorkAreaList | null>(null);
  const [equipment, setEquipment] = useState<Equipment[]>([]);
  const [linked, setLinked] = useState<string[]>([]);
  const [zonePpe, setZonePpe] = useState<ZonePpe[]>([]);
  const [policy, setPolicy] = useState<WorkAreaPolicy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);

  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const imageRef = useRef<string | null>(null);

  const fail = (e: Error) => setError(errorText(e));

  const reload = useCallback(
    () =>
      fetchWorkAreas(camera_id).then((l) => {
        setList(l);
        return l;
      }),
    [camera_id],
  );

  useEffect(() => {
    if (MOCK_AUTH) return;
    void Promise.all([
      fetchCameras(),
      fetchWorkAreas(camera_id),
      fetchEquipment(),
      fetchCameraEquipment(camera_id),
      fetchZones(),
    ])
      .then(([cams, l, eq, link, zones]) => {
        const cam = cams.find((c) => c.camera_id === camera_id) ?? null;
        setCamera(cam);
        setList(l);
        setEquipment(eq);
        setLinked(link.equipment_ids);
        setZonePpe(
          zones.find((z) => z.zone_name === cam?.zone_name)?.required_ppe ?? [],
        );
      })
      .catch((e: Error) => setError(errorText(e)));
  }, [camera_id]);

  // When area mode is on, ask the server whether the saved setup is complete
  useEffect(() => {
    if (!list?.enabled) return;
    let stale = false;
    fetchWorkAreaPolicy(camera_id)
      .then((p) => !stale && setPolicy(p))
      .catch(() => {});
    return () => {
      stale = true;
    };
  }, [camera_id, list?.enabled, list?.version]);

  // Poll a pending snapshot; the image is shown once the server finished hiding faces
  const snapId = snapshot?.snapshot_id;
  const snapStatus = snapshot?.status;
  useEffect(() => {
    if (!snapId || (snapStatus !== 'queued' && snapStatus !== 'running'))
      return;
    let stale = false;
    const timer = window.setTimeout(() => {
      fetchSnapshot(camera_id, snapId)
        .then((s) => !stale && setSnapshot(s))
        .catch((e: Error) => !stale && setError(errorText(e)));
    }, 1500);
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
  }, [camera_id, snapId, snapStatus, snapshot]);

  useEffect(() => {
    if (snapshot?.status !== 'ready' || !snapshot.image_url) return;
    let stale = false;
    fetchSnapshotImage(snapshot.image_url)
      .then((blob) => {
        if (stale) return;
        const url = URL.createObjectURL(blob);
        if (imageRef.current) URL.revokeObjectURL(imageRef.current);
        imageRef.current = url;
        setImageUrl(url);
      })
      .catch((e: Error) => !stale && setError(errorText(e)));
    return () => {
      stale = true;
    };
  }, [snapshot]);

  useEffect(
    () => () => {
      if (imageRef.current) URL.revokeObjectURL(imageRef.current);
    },
    [],
  );

  const getSnapshot = () => {
    setError(null);
    setSnapshot(null);
    setAsking(true);
    requestSnapshot(camera_id)
      .then(setSnapshot)
      .catch(fail)
      .finally(() => setAsking(false));
  };

  const usable = equipment.filter(
    (e) => linked.includes(e.equipment_id) && e.status === 'active',
  );
  const eqOf = (id: string) => equipment.find((e) => e.equipment_id === id);

  const startDraft = (a?: WorkArea) => {
    setError(null);
    setNotice(null);
    setDraft(
      a
        ? {
            id: a.area_id,
            version: a.version,
            name: a.name,
            equipmentId: a.equipment_id,
            points: a.points,
          }
        : {
            id: 'new',
            version: 0,
            name: '',
            equipmentId: usable[0]?.equipment_id ?? '',
            points: [],
          },
    );
  };

  const problem = draft ? polygonProblem(draft.points) : null;
  const canSave =
    !!draft && !busy && !problem && draft.name.trim() && draft.equipmentId;

  const save = () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    const input = {
      name: draft.name.trim(),
      equipment_id: draft.equipmentId,
      points: draft.points,
    };
    (draft.id === 'new'
      ? createWorkArea(camera_id, input)
      : updateWorkArea(camera_id, draft.id, input, draft.version)
    )
      .then(() => {
        setDraft(null);
        setNotice('영역을 저장했습니다.');
        return reload();
      })
      .catch(fail)
      .finally(() => setBusy(false));
  };

  const remove = (a: WorkArea) => {
    setBusy(true);
    setError(null);
    deleteWorkArea(camera_id, a.area_id, a.version)
      .then((l) => {
        setList(l);
        setNotice('영역을 삭제했습니다.');
      })
      .catch(fail)
      .finally(() => setBusy(false));
  };

  const toggleMode = (enabled: boolean) => {
    if (!list) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    setWorkAreaMode(camera_id, enabled, list.version)
      .then((l) => {
        setList(l);
        if (!enabled) setPolicy(null);
        setNotice(
          enabled
            ? '영역 모드를 켰습니다.'
            : '영역 모드를 껐습니다. 카메라 전체 규칙으로 돌아갑니다.',
        );
      })
      .catch(fail)
      .finally(() => setBusy(false));
  };

  if (MOCK_AUTH)
    return (
      <div className={styles.page}>
        <p className={styles.desc}>
          목업 모드에서는 작업 영역을 사용할 수 없습니다.
        </p>
      </div>
    );

  const others = (list?.items ?? [])
    .filter((a) => a.area_id !== draft?.id)
    .map((a) => ({ id: a.area_id, name: a.name, points: a.points }));
  const noImage = !imageUrl;

  return (
    <div className={styles.page}>
      <Link to="/cameras" className={styles.back}>
        ← 카메라 관리
      </Link>
      <h2 className={styles.title}>
        {camera_id} 작업 영역
        {camera?.camera_location && (
          <span className={styles.sub}> · {camera.camera_location}</span>
        )}
      </h2>
      <p className={styles.desc}>
        장비 주변을 화면 위에 표시하면, 그 안에 서 있는 사람에게만 그 장비의
        추가 PPE가 적용됩니다. 영역 밖에서는 구역 기본 PPE만 적용됩니다. 사람의
        발 위치(박스 아래 가운데)가 영역 안에 있는지로 판단하며, 실제 거리를
        재지는 않습니다.
      </p>

      {error && <p className={styles.errorMsg}>⚠ {error}</p>}
      {notice && <p className={styles.okMsg}>{notice}</p>}

      {/* ── 1. Reference image ── */}
      <section className={styles.card}>
        <div className={styles.head}>
          <h3 className={styles.cardTitle}>1. 기준 화면</h3>
          <button
            type="button"
            className={styles.btn}
            disabled={
              asking ||
              snapshot?.status === 'queued' ||
              snapshot?.status === 'running'
            }
            onClick={getSnapshot}
          >
            {imageUrl ? '다시 가져오기' : '기준 화면 가져오기'}
          </button>
        </div>
        <p className={styles.help}>
          서버가 받은 최근 영상의 한 장면을 얼굴을 가린 뒤 보여 줍니다. 원본은
          보여 주지 않으며 서버에는 2분만 남습니다. 카메라 위치나 각도가 바뀌면
          영역을 다시 그려야 합니다.
        </p>
        {snapshot && snapshot.status !== 'ready' && (
          <p
            className={
              snapshot.status === 'failed' ? styles.errorMsg : styles.help
            }
          >
            {SNAPSHOT_STATE[snapshot.status]}
            {snapshot.error_code &&
              ` (${AREA_ERRORS[snapshot.error_code] ?? snapshot.error_code})`}
          </p>
        )}
        {imageUrl && (
          <>
            <PolygonEditor
              imageUrl={imageUrl}
              points={draft?.points ?? []}
              others={others}
              editing={!!draft}
              onChange={(points) => setDraft((d) => (d ? { ...d, points } : d))}
            />
            <p className={styles.help}>
              {draft
                ? `화면을 눌러 꼭짓점을 찍고(최대 ${MAX_POINTS}개), 점을 끌어 위치를 고치세요. 흰 점이 첫 점입니다.`
                : '아래에서 영역을 추가하거나 수정하면 이 화면에서 그릴 수 있습니다.'}
            </p>
          </>
        )}
      </section>

      {/* ── 2. Areas ── */}
      <section className={styles.card}>
        <div className={styles.head}>
          <h3 className={styles.cardTitle}>
            2. 영역 ({list?.items.length ?? 0})
          </h3>
          {!draft && (
            <button
              type="button"
              className={styles.btn}
              disabled={noImage || usable.length === 0}
              onClick={() => startDraft()}
            >
              + 영역 추가
            </button>
          )}
        </div>
        {usable.length === 0 && list && (
          <p className={styles.help}>
            이 카메라에 연결된 사용 중 장비가 없습니다.{' '}
            <Link to="/equipment">장비 관리</Link>에서 장비를 카메라에 연결한 뒤
            영역을 만드세요.
          </p>
        )}
        {noImage && usable.length > 0 && (
          <p className={styles.help}>
            영역을 그리려면 먼저 기준 화면을 가져오세요.
          </p>
        )}
        {!list && !error && <p className={styles.help}>불러오는 중...</p>}
        {list?.items.length === 0 && !draft && (
          <p className={styles.help}>
            만든 영역이 없습니다. 지금은 카메라 전체에 같은 규칙이 적용됩니다.
          </p>
        )}

        {list?.items.map((a) =>
          draft?.id === a.area_id ? null : (
            <div key={a.area_id} className={styles.item}>
              <div className={styles.itemHead}>
                <strong>{a.name}</strong>
                <span className={styles.sub}>
                  {eqOf(a.equipment_id)?.name ?? a.equipment_id}
                </span>
              </div>
              <div className={styles.preview}>
                <span className={styles.previewLabel}>
                  이 영역 안에서 필요한 PPE
                </span>
                <PpeChips
                  value={combine(
                    zonePpe,
                    eqOf(a.equipment_id)?.required_ppe ?? [],
                  )}
                  emptyText="필요한 PPE 없음"
                />
              </div>
              {!draft && (
                <div className={styles.actions}>
                  <button
                    type="button"
                    className={styles.btn}
                    disabled={busy || noImage}
                    title={
                      noImage
                        ? '수정하려면 기준 화면을 먼저 가져오세요'
                        : undefined
                    }
                    onClick={() => startDraft(a)}
                  >
                    수정
                  </button>
                  <ConfirmButton
                    className={styles.danger}
                    label="삭제"
                    question="영역을 삭제할까요?"
                    confirmLabel="삭제"
                    disabled={busy}
                    onConfirm={() => remove(a)}
                  />
                </div>
              )}
            </div>
          ),
        )}

        {draft && (
          <div className={styles.form}>
            <label className={styles.field}>
              영역 이름
              <input
                className={styles.input}
                value={draft.name}
                maxLength={100}
                placeholder="예: 프레스 앞 작업 위치"
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </label>
            <label className={styles.field}>
              장비
              <select
                className={styles.input}
                value={draft.equipmentId}
                onChange={(e) =>
                  setDraft({ ...draft, equipmentId: e.target.value })
                }
              >
                {usable.map((e) => (
                  <option key={e.equipment_id} value={e.equipment_id}>
                    {e.name}
                  </option>
                ))}
              </select>
            </label>
            {draft.equipmentId && (
              <div className={styles.preview}>
                <span className={styles.previewLabel}>
                  이 영역 안에서 필요한 PPE
                </span>
                <PpeChips
                  value={combine(
                    zonePpe,
                    eqOf(draft.equipmentId)?.required_ppe ?? [],
                  )}
                  emptyText="필요한 PPE 없음"
                />
              </div>
            )}
            <p
              className={
                draft.points.length && problem ? styles.errorMsg : styles.help
              }
            >
              꼭짓점 {draft.points.length}개
              {draft.points.length > 0 && problem && ` · ${problem}`}
            </p>
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.btn}
                disabled={draft.points.length === 0}
                onClick={() =>
                  setDraft({ ...draft, points: draft.points.slice(0, -1) })
                }
              >
                마지막 점 지우기
              </button>
              <button
                type="button"
                className={styles.btn}
                disabled={draft.points.length === 0}
                onClick={() => setDraft({ ...draft, points: [] })}
              >
                모두 지우기
              </button>
              <button
                type="button"
                className={styles.btn}
                onClick={() => setDraft(null)}
              >
                취소
              </button>
              <button
                type="button"
                className={styles.primary}
                disabled={!canSave}
                onClick={save}
              >
                {busy ? '저장 중…' : '저장'}
              </button>
            </div>
          </div>
        )}
      </section>

      {/* ── 3. Mode ── */}
      <section className={styles.card}>
        <div className={styles.head}>
          <h3 className={styles.cardTitle}>3. 영역 모드</h3>
          <span
            className={`${styles.badge} ${list?.enabled ? styles.on : styles.off}`}
          >
            {list?.enabled ? '켜짐' : '꺼짐'}
          </span>
        </div>
        <p className={styles.help}>
          {list?.enabled
            ? '영역 안에서만 장비 PPE가 추가되고, 영역 밖은 구역 기본 PPE만 적용됩니다.'
            : '꺼져 있으면 영역과 관계없이 카메라 전체에 연결된 모든 장비의 PPE가 적용됩니다.'}
        </p>
        {list?.enabled && policy?.configured === false && (
          <p className={styles.errorMsg}>
            ⚠ 설정에 문제가 있어 이벤트 판정이 멈춰 있습니다:{' '}
            {(policy.configuration_issues ?? []).map(describeIssue).join(' / ')}
          </p>
        )}
        {list?.enabled ? (
          <ConfirmButton
            className={styles.btn}
            label="영역 모드 끄기"
            question="카메라 전체 규칙으로 돌아갑니다."
            confirmLabel="끄기"
            danger={false}
            disabled={busy}
            onConfirm={() => toggleMode(false)}
          />
        ) : (
          <>
            <p className={styles.warn}>
              서버의 추론 프로그램이 영역을 지원하기 전에 켜면, 이 카메라의 기존
              이벤트 판정이 <strong>멈춥니다</strong>. 추론 담당이 영역 적용을
              연결한 것을 확인한 뒤에만 켜세요.
            </p>
            <ConfirmButton
              className={styles.btn}
              label="영역 모드 켜기"
              question="추론이 영역을 지원하지 않으면 이 카메라의 이벤트 판정이 멈춥니다. 계속할까요?"
              confirmLabel="켜기"
              disabled={busy || !list || list.items.length === 0}
              onConfirm={() => toggleMode(true)}
            />
          </>
        )}
      </section>
    </div>
  );
}
