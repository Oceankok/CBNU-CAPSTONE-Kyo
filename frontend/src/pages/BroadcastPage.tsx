import { useState, useEffect, useCallback } from 'react';
import {
  fetchBroadcastSettings,
  saveBroadcastSettings,
} from '../api/broadcast';
import { fetchZones } from '../api/zones';
import {
  BROADCAST_LANGS,
  SAMPLE_MESSAGES,
  isSampleOrEmpty,
} from '../broadcast';
import type {
  BroadcastSettings,
  BroadcastTemplate,
  BroadcastLanguage,
} from '../types';
import styles from './BroadcastPage.module.css';

// Default template when user adds a new row
const BLANK_TEMPLATE: BroadcastTemplate = {
  ppe_type: 'helmet',
  zone_name: '',
  language: 'ko',
  message: '',
};

// Fallback defaults used when the backend has no settings yet
const DEFAULT_SETTINGS: BroadcastSettings = {
  enabled: false,
  default_language: 'ko',
  cooldown_sec: 30,
  templates: [
    {
      ppe_type: 'helmet',
      zone_name: '',
      language: 'ko',
      message: '해당 작업 구역의 작업자는 안전모 착용 상태를 확인해 주세요.',
    },
    {
      ppe_type: 'vest',
      zone_name: '',
      language: 'ko',
      message: '해당 작업 구역의 작업자는 안전조끼 착용 상태를 확인해 주세요.',
    },
  ],
};

const PPE_LABEL: Record<string, string> = {
  helmet: '안전모',
  vest: '안전조끼',
};

export default function BroadcastPage() {
  const [settings, setSettings] = useState<BroadcastSettings>(DEFAULT_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [zones, setZones] = useState<string[]>([]);
  const [addLang, setAddLang] = useState<BroadcastLanguage | ''>('');

  useEffect(() => {
    fetchZones()
      .then((z) => setZones(z.map((x) => x.zone_name)))
      .catch(() => {}); // the selector still shows each template's current zone
    fetchBroadcastSettings()
      .then(setSettings)
      .catch(() => {
        // Backend API not yet implemented — fall back to default settings
        setSettings(DEFAULT_SETTINGS);
      })
      .finally(() => setLoading(false));
  }, []);

  const handleSave = useCallback(() => {
    setSaving(true);
    setError(null);
    saveBroadcastSettings(settings)
      .then(setSettings)
      .catch(() => {
        // Show a warning but keep edits so the user doesn't lose work
        setError(
          '저장 실패: 백엔드 API가 아직 구현 중입니다. 설정은 로컬에서만 유지됩니다.',
        );
      })
      .finally(() => {
        setSaving(false);
        setSuccessMsg('설정이 저장되었습니다.');
        setTimeout(() => setSuccessMsg(null), 3000);
      });
  }, [settings]);

  function updateField<K extends keyof BroadcastSettings>(
    key: K,
    value: BroadcastSettings[K],
  ) {
    setSettings((prev) => ({ ...prev, [key]: value }));
  }

  function updateTemplate(index: number, patch: Partial<BroadcastTemplate>) {
    setSettings((prev) => {
      const templates = prev.templates.map((t, i) =>
        i === index ? { ...t, ...patch } : t,
      );
      return { ...prev, templates };
    });
  }

  function addTemplate() {
    setSettings((prev) => ({
      ...prev,
      templates: [...prev.templates, { ...BLANK_TEMPLATE }],
    }));
  }

  const hasLang = (code: BroadcastLanguage) =>
    settings.templates.some((t) => t.language === code);

  function addLanguage() {
    if (!addLang) return;
    const language = addLang;
    setSettings((prev) => ({
      ...prev,
      templates: [
        ...prev.templates,
        ...(['helmet', 'vest'] as const).map((ppe_type) => ({
          ppe_type,
          zone_name: '',
          language,
          message: SAMPLE_MESSAGES[language][ppe_type],
        })),
      ],
    }));
    setAddLang('');
  }

  function removeTemplate(index: number) {
    setSettings((prev) => ({
      ...prev,
      templates: prev.templates.filter((_, i) => i !== index),
    }));
  }

  if (loading) {
    return <p style={{ color: '#718096' }}>설정을 불러오는 중...</p>;
  }

  return (
    <div className={styles.page}>
      {/* Page header */}
      <p className={styles.desc}>
        PPE 미착용 감지 시 현장에 송출할 경고 방송을 설정합니다.
      </p>

      {error && <p className={styles.errorMsg}>⚠ {error}</p>}
      {successMsg && <p className={styles.successMsg}>✓ {successMsg}</p>}

      {/* ─── Global settings ─────────────────────────────────── */}
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>전체 설정</h3>

        <div className={styles.fieldRow}>
          <label className={styles.fieldLabel}>경고 방송 사용</label>
          <button
            className={`${styles.toggleBtn} ${settings.enabled ? styles.toggleOn : styles.toggleOff}`}
            onClick={() => updateField('enabled', !settings.enabled)}
            aria-pressed={settings.enabled}
          >
            {settings.enabled ? 'ON' : 'OFF'}
          </button>
          <span className={styles.fieldHint}>
            {settings.enabled
              ? '방송이 활성화되어 있습니다.'
              : '방송이 비활성화되어 있습니다.'}
          </span>
        </div>

        <div className={styles.fieldRow}>
          <label className={styles.fieldLabel}>기본 방송 언어</label>
          <select
            className={styles.select}
            value={settings.default_language}
            onChange={(e) =>
              updateField(
                'default_language',
                e.target.value as BroadcastLanguage,
              )
            }
          >
            {BROADCAST_LANGS.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </div>

        <div className={styles.fieldRow}>
          <label className={styles.fieldLabel}>중복 방송 방지 (cooldown)</label>
          <div className={styles.fieldInline}>
            <input
              type="number"
              className={styles.numberInput}
              min={0}
              max={3600}
              value={settings.cooldown_sec}
              onChange={(e) =>
                updateField('cooldown_sec', Number(e.target.value))
              }
            />
            <span className={styles.unit}>초</span>
          </div>
          <span className={styles.fieldHint}>
            동일 상황에 대해 재방송까지 대기 시간
          </span>
        </div>
      </section>

      {/* ─── Message templates ───────────────────────────────── */}
      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h3 className={styles.sectionTitle}>PPE 유형별 메시지 템플릿</h3>
          <button className={styles.addBtn} onClick={addTemplate}>
            + 템플릿 추가
          </button>
        </div>
        <p className={styles.sectionDesc}>
          구역을 "전체 구역"으로 두면 모든 구역에 적용되고, 구역을 고르면 해당
          구역에서만 사용됩니다.
        </p>

        {/* Add helmet + vest sentences for one more language in a single step */}
        <div className={styles.addLang}>
          <select
            className={styles.select}
            value={addLang}
            onChange={(e) =>
              setAddLang(e.target.value as BroadcastLanguage | '')
            }
          >
            <option value="">+ 언어 추가</option>
            {BROADCAST_LANGS.filter((l) => !hasLang(l.code)).map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
          <button
            className={styles.addBtn}
            disabled={!addLang}
            onClick={addLanguage}
          >
            안전모·안전조끼 문구 추가
          </button>
        </div>
        <p className={styles.sectionDesc}>
          추가되는 문구는 예시입니다. 실제 방송 전에 해당 언어를 아는 사람이
          확인해 주세요.
        </p>

        {settings.templates.length === 0 && (
          <p style={{ color: '#718096', fontSize: 13 }}>
            등록된 템플릿이 없습니다.
          </p>
        )}

        <div className={styles.templateList}>
          {settings.templates.map((tpl, idx) => (
            <div key={idx} className={styles.templateCard}>
              <div className={styles.templateMeta}>
                {/* PPE type */}
                <div className={styles.metaField}>
                  <span className={styles.metaLabel}>PPE 유형</span>
                  <select
                    className={styles.select}
                    value={tpl.ppe_type}
                    onChange={(e) => {
                      const ppe_type = e.target.value as 'helmet' | 'vest';
                      updateTemplate(idx, {
                        ppe_type,
                        ...(isSampleOrEmpty(tpl.message)
                          ? { message: SAMPLE_MESSAGES[tpl.language][ppe_type] }
                          : {}),
                      });
                    }}
                  >
                    <option value="helmet">{PPE_LABEL.helmet}</option>
                    <option value="vest">{PPE_LABEL.vest}</option>
                  </select>
                </div>

                {/* Zone */}
                <div className={styles.metaField}>
                  <span className={styles.metaLabel}>구역명 (선택)</span>
                  {/* A typed zone name that does not match exactly never gets used, so pick from registered zones */}
                  <select
                    className={styles.select}
                    value={tpl.zone_name}
                    onChange={(e) =>
                      updateTemplate(idx, { zone_name: e.target.value })
                    }
                  >
                    <option value="">전체 구역</option>
                    {[
                      ...new Set([
                        ...zones,
                        ...(tpl.zone_name ? [tpl.zone_name] : []),
                      ]),
                    ].map((z) => (
                      <option key={z} value={z}>
                        {z}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Language */}
                <div className={styles.metaField}>
                  <span className={styles.metaLabel}>언어</span>
                  <select
                    className={styles.select}
                    value={tpl.language}
                    onChange={(e) => {
                      const language = e.target.value as BroadcastLanguage;
                      // Swap an untouched starter sentence for the new language's one; keep anything typed by hand
                      updateTemplate(idx, {
                        language,
                        ...(isSampleOrEmpty(tpl.message)
                          ? { message: SAMPLE_MESSAGES[language][tpl.ppe_type] }
                          : {}),
                      });
                    }}
                  >
                    {BROADCAST_LANGS.map((l) => (
                      <option key={l.code} value={l.code}>
                        {l.label}
                      </option>
                    ))}
                  </select>
                </div>

                <button
                  className={styles.removeBtn}
                  onClick={() => removeTemplate(idx)}
                  title="삭제"
                >
                  ✕
                </button>
              </div>

              {/* Message textarea */}
              <textarea
                className={styles.messageInput}
                rows={2}
                placeholder="방송할 메시지를 입력하세요."
                value={tpl.message}
                onChange={(e) =>
                  updateTemplate(idx, { message: e.target.value })
                }
              />
            </div>
          ))}
        </div>
      </section>

      {/* Save button */}
      <div className={styles.footer}>
        <button
          className={styles.saveBtn}
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? '저장 중…' : '설정 저장'}
        </button>
      </div>
    </div>
  );
}
