// Admin settings sub-pages, grouped under the 설정 tab
export const SETTINGS_ITEMS = [
  { to: '/users', label: '작업자 관리', desc: '계정 추가·정지, 담당 구역 지정' },
  { to: '/zones', label: '구역 규칙', desc: '구역별 필수 PPE와 안전 수칙' },
  { to: '/equipment', label: '장비 관리', desc: '장비별 추가 PPE, 카메라별 적용 PPE' },
  { to: '/broadcast', label: '경고 방송', desc: '방송 사용 여부, 언어, 메시지 템플릿' },
  { to: '/nodes', label: '현장 PC', desc: '방송 PC 연결 상태, 음성·언어, 테스트 방송, 카메라 연결' },
];
