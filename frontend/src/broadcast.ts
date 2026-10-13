import type { BroadcastLanguage } from './types';

// Languages a field PC can speak (backend INSTALL_LANGUAGES, by primary subtag: en-US / en-GB -> en)
export const BROADCAST_LANGS: { code: BroadcastLanguage; label: string }[] = [
  { code: 'ko', label: '한국어' },
  { code: 'en', label: 'English' },
  { code: 'ja', label: '日本語 (일본어)' },
  { code: 'zh', label: '中文 (중국어)' },
  { code: 'de', label: 'Deutsch (독일어)' },
  { code: 'fr', label: 'Français (프랑스어)' },
  { code: 'es', label: 'Español (스페인어)' },
];

export const langLabel = (code: string) =>
  BROADCAST_LANGS.find((l) => l.code === code)?.label ?? code;

// Starter sentences so an admin does not have to write every language from scratch.
// ponytail: drafted text, not reviewed by native speakers — admins should check before real use
export const SAMPLE_MESSAGES: Record<
  BroadcastLanguage,
  { helmet: string; vest: string }
> = {
  ko: {
    helmet: '해당 작업 구역의 작업자는 안전모 착용 상태를 확인해 주세요.',
    vest: '해당 작업 구역의 작업자는 안전조끼 착용 상태를 확인해 주세요.',
  },
  en: {
    helmet: 'Workers in this area, please check your helmet.',
    vest: 'Workers in this area, please check your safety vest.',
  },
  ja: {
    helmet: 'この作業区域の作業者は、ヘルメットの着用を確認してください。',
    vest: 'この作業区域の作業者は、安全ベストの着用を確認してください。',
  },
  zh: {
    helmet: '本作业区域的工作人员，请确认已佩戴安全帽。',
    vest: '本作业区域的工作人员，请确认已穿着安全背心。',
  },
  de: {
    helmet: 'Mitarbeiter in diesem Bereich, bitte überprüfen Sie Ihren Helm.',
    vest: 'Mitarbeiter in diesem Bereich, bitte überprüfen Sie Ihre Warnweste.',
  },
  fr: {
    helmet:
      'Aux personnes travaillant dans cette zone : veuillez vérifier le port de votre casque.',
    vest: 'Aux personnes travaillant dans cette zone : veuillez vérifier le port de votre gilet de sécurité.',
  },
  es: {
    helmet:
      'Trabajadores de esta zona, por favor verifiquen que llevan el casco puesto.',
    vest: 'Trabajadores de esta zona, por favor verifiquen que llevan el chaleco de seguridad puesto.',
  },
};

// True when the text is empty or one of the untouched starter sentences (safe to replace automatically)
export const isSampleOrEmpty = (message: string) =>
  !message.trim() ||
  Object.values(SAMPLE_MESSAGES).some(
    (m) => m.helmet === message || m.vest === message,
  );
