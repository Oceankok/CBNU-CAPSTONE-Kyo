import type { ZonePpe } from './types';

// Mirrors backend/zones/policy.py ALLOWED_PPE; `detected` = the current YOLO model can check it
export const ZONE_PPE: {
  value: ZonePpe;
  icon: string;
  label: string;
  detected: boolean;
}[] = [
  { value: 'helmet', icon: '🪖', label: '안전모', detected: true },
  { value: 'vest', icon: '🦺', label: '안전조끼', detected: true },
  { value: 'goggles', icon: '🥽', label: '보안경', detected: false },
  { value: 'gloves', icon: '🧤', label: '안전장갑', detected: false },
  { value: 'safety_shoes', icon: '🥾', label: '안전화', detected: false },
  {
    value: 'hearing_protection',
    icon: '🎧',
    label: '청력 보호구',
    detected: false,
  },
  { value: 'mask', icon: '😷', label: '마스크', detected: false },
  { value: 'harness', icon: '🪢', label: '안전대', detected: false },
];

// Unknown codes (backend added one) fall back to the raw code instead of crashing
export const ppeInfo = (code: string) =>
  ZONE_PPE.find((p) => p.value === code) ?? {
    value: code,
    icon: '🦺',
    label: code,
    detected: false,
  };
