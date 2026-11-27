// 화면 여러 칸이 함께 쓰는 것: 요소 찾기($), 글자 이스케이프(esc), 함께 쓰는 상태(st).
// st 의 값은 여러 파일이 읽고 바꿉니다 (예: st.src = 지금 넣은 수치지도).
const $=id=>document.getElementById(id);
const esc=t=>String(t??'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
export const st = {
  src: null,
  site: null,
  names: {},
  lawRing: null,
  center: null,
  rangeEdit: false,
  centerMoved: false,
  pendingSite: null,
  drawing: null,
  resultReady: false,
  nLog: 0,
  nEv: 0,
  live: undefined,   // 진행 화면 실황 (ui_tabs.js 가 처음에 만듦)
  timer: null,
  resImg: 'preview_plan.png',
  RSUB: [['평면','pplan','preview_plan.png'],['3D','p3d'],['기록','plog']],
  recentItems: [],
  RES: ['pplan','p3d','plog'],
  lastRes: 'pplan',
  lastBody: null,
};
export {$, esc};
