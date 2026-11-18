// 생성 과정 실황: '2D 작업 → 3D 부품' 대응표 + 실시간 3D 보기 + 지금 2D 그림.
// 서버(/status)가 주는 events 를 차례로 받아 화면을 채운다.
//   step  {id, r, n, x}        2D 작업 한 칸 시작
//   part  {key, url, replaces} 3D 부품 GLB (같은 key 는 바꿔 끼움, replaces 는 치움, 'sheet_*' 처럼 끝 * 허용)
//   plan  {key, url}           지금 2D 평면 그림
//   explode / done
import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {smartZoom} from './zoom.js';

// 단계 번호 → [이름, 하는 일, 이유]
const SUB = {
  '0.1': ['zip 풀기', '한글 파일명을 cp949로 복원하고 zip 안 zip도 풉니다.', 'EUC-KR 파일명이라 그냥 풀면 깨집니다.'],
  '0.2': ['수치지도 확인', '건물 레이어가 없으면 이유를 알리고 멈춥니다.', '층수·용도 없이는 모델 절반이 성립하지 않습니다.'],
  '0.3': ['좌표계', '.prj에서 좌표계를 읽고, 없으면 EPSG 5186으로 봅니다.', 'm 단위 좌표라야 판 높이 계산이 맞습니다.'],
  '0.4': ['가운데 찾기', '고른 건물·지점을 모델 가운데로 잡습니다.', '여러 동이 걸리면 합친 중심이 단지 가운데입니다.'],
  '0.5': ['범위 상자', '가운데 ± 크기/2. 3D에서는 같은 크기의 받침판이 됩니다.', '자르는 것보다 밀어 넣어야 모형 크기가 지켜집니다.'],
  '0.6': ['NDVI 합성', '여름철 위성영상의 NDVI 최댓값을 합성합니다.', '구름 낀 장면을 다른 장면이 메웁니다.'],
  '1.1': ['등고선 +200m', '등고선을 읽어 3D에서 제 높이에 띄웁니다.', '보간은 주변 점이 있어야 테두리도 정확합니다.'],
  '1.2': ['건물 바로잡기', '꼬인 외곽선을 고칩니다.', '자기교차 그대로면 돌출이 실패합니다.'],
  '1.3': ['길 레이어 걸러 읽기', '교통섬·계단만 골라 범위로 자릅니다.', '같은 레이어에 다른 시설이 섞여 있습니다.'],
  '2.1': ['점 만들기', '등고선을 5m 간격 점으로 바꿉니다.', '일정 간격 점이면 충분합니다.'],
  '2.2': ['TIN 보간', '삼각망으로 지형면을 만듭니다. 3D에서 곡면이 됩니다.', '원래 등고선을 정확히 지나갑니다.'],
  '2.3': ['0.5m 격자 · σ4m', '격자로 높이를 찍고 부드럽게 고릅니다.', '판 윤곽이 톱니가 되지 않게 합니다.'],
  '2.4': ['판 높이', '바닥 높이부터 판 간격마다 단을 나눕니다.', '1:1000에서 5m = 5mm 판.'],
  '2.5': ['"이 높이 이상" 영역', '평면 영역 하나가 3D에서 판 한 장이 됩니다.', '항상 닫힌 판이 나옵니다.'],
  '2.6': ['자를 수 있게 다듬기', '가는 띠와 작은 조각을 없앱니다.', '레이저로 자를 수 없는 조각을 없앱니다.'],
  '2.7': ['윗판 ⊂ 아랫판', '각 판을 아래 판 안으로 맞춥니다.', '튀어나오면 쌓을 수 없습니다.'],
  '3.1': ['범위로 자르기', '잘린 건물도 남기고 작은 것은 버립니다.', '실제 콘타 모형과 같습니다.'],
  '3.2': ['놓일 판 찾기', '건물 바닥이 될 판 높이를 찾습니다.', '경사지 건물 밑 틈을 없앱니다.'],
  '3.3': ['층수 × 층 높이', '발자국을 층수만큼 올려 3D 매스로 돌출합니다.', '층수 출처를 기록합니다.'],
  '3.4': ['용도·속성', '용도별 레이어와 건물 정보를 붙입니다.', '용도별로 켜고 끄기 쉽게.'],
  '4.1': ['종류별 면', '차도·보도·교통섬·계단으로 나눕니다.', '종류마다 표현이 달라야 읽힙니다.'],
  '4.2': ['겹침 정리', '작은 것이 위에 오게 합니다.', '차도가 교통섬을 덮지 않게.'],
  '4.3': ['판마다 나누기', '판마다 드러난 길만 남깁니다.', '도안은 판 한 장씩입니다.'],
  '4.4': ['차도 끼움', '평면 차도 영역이 3D에서 판 두께 조각이 되고 판은 파입니다.', '같은 선으로 잘라 끼우면 꼭 맞습니다.'],
  '4.5': ['곡면 위 길 층', '길을 곡면 위에 두께 있는 층으로 얹습니다.', '얇은 면은 곡면을 뚫고 깜빡입니다.'],
  '4.6': ['면 없는 길', '도로경계선만 각인합니다.', '없는 면은 지어내지 않습니다.'],
  '5.1': ['가로수 대장', '서울시 가로수 대장에서 범위 안 나무의 실제 위치와 수종을 가져옵니다. 수종마다 수관 크기가 다릅니다.', '구청이 관리하는 실제 나무라 그대로 믿습니다.'],
  '5.2': ['항공사진 보충', '대장에 없는 나무만 항공사진에서 보도 위 초록 수관으로 찾아 보충합니다. 3D에서 줄기 + 타원구 나무가 됩니다.', '일정 간격으로 지어내지 않고 실제 있는 나무만 넣습니다.'],
  '5.3': ['수관 크기·줄기 자리', '수관 넓이로 크기를 정하고 줄기는 보도 안에 둡니다.', '가지는 차도 위로 뻗어도 줄기는 보도에 섭니다.'],
  '5.4': ['숲 / 조경 판정', '위성 NDVI로 녹지를 찾습니다.', '진하고 넓으면 숲, 옅고 작으면 조경.'],
  '5.5': ['녹지 육각 격자', '녹지에 흔들린 격자로 나무를 채웁니다.', '반듯하면 과수원, 무작위면 뭉칩니다.'],
  '5.6': ['타원구 나무', '세로 1.6배 타원구.', '수형이 읽히게.'],
  '6.1': ['경사 깎기', '평면 높이 격자가 3D에서 계단 위 경사 매스가 됩니다.', '판 모서리와 정확히 맞물립니다.'],
  '6.2': ['건물 터', '건물 밑을 평평하게 깎고 둘레에서 이어 붙입니다.', '건물이 뜨거나 묻히지 않게.'],
  '6.3': ['건물 다시 앉히기', '바닥을 터보다 0.3m 낮춥니다.', '실틈이 안 보이게.'],
  '6.4': ['경사 매스', '계단 윗면부터 경사면까지 쐐기.', '따로 켜고 끌 수 있습니다.'],
  '6.5': ['곡면에 얹기', '나무를 곡면 위로 올립니다.', '묻히지 않게.'],
  '7.1': ['건물 방향', '긴 축을 찾습니다.', '옥탑이 동 방향으로 놓입니다.'],
  '7.2': ['경사지붕', '처마까지 거리로 지붕 높이를 냅니다. 3D 매스 위에 지붕이 생깁니다.', 'ㄱ·ㄷ자 집도 모임지붕이 됩니다.'],
  '7.3': ['캐노피', '무벽건물은 지붕판과 기둥.', '벽 없이.'],
  '7.4': ['공사중 골조', '슬래브와 코어.', '짓는 중으로 읽히게.'],
  '7.5': ['옥탑', '아파트 코어 옥탑, 계단실.', '코어 간격을 따릅니다.'],
  '7.6': ['파라펫·층선', '평지붕 파라펫, 층선.', '기본형.'],
  '8.1': ['원점 옮기기', '범위 왼쪽 아래를 (0,0)으로.', '화면 떨림 방지.'],
  '8.2': ['Rhino 3dm', '부품마다 레이어와 정보를 붙여 씁니다.', '선택이 쉬워집니다.'],
  '8.3': ['STL · GLB', '하나의 메시로 씁니다.', '3D 프린트·미리보기용.'],
  '8.4': ['스케치업', '.dae와 확장을 씁니다.', '.skp는 직접 못 씁니다.'],
  '9.1': ['m → mm', '실제 모형 크기로 바꿉니다.', '배율 실수 방지.'],
  '9.2': ['계단 높이 기준', '길·나무를 평평한 판에 배치합니다.', '구멍이 엉뚱한 데 나지 않게.'],
  '9.3': ['다음 판 자리 각인', '판마다 윗판 자리를 새깁니다. 3D에서 판을 띄워 순서를 보여줍니다.', '조립 때 위치가 보입니다.'],
  '9.4': ['건물 자리 각인', '건물 붙일 자리를 새깁니다.', '붙일 자리가 있어야 합니다.'],
  '9.5': ['파일 쓰기', '판마다 DXF 한 장.', '정렬하면 쌓는 순서.']
};
// 3D 부품 줄: [key, 이름, 설명]
const N3 = [
  ['base', '받침판', '범위 크기 바닥판'], ['c3d', '등고선 띄우기', '선을 제 높이로'], ['tin', '지형면', '삼각망 곡면'],
  ['sheet', '계단판 솔리드', '판마다 한 장씩 돌출'], ['mass', '건물 매스', '발자국 × 높이'], ['inlay', '차도 끼움 조각', '판 두께, 판은 파냄'],
  ['path', '곡면 위 길 층', '두께 있는 솔리드'], ['tree', '나무', '줄기 + 타원구'], ['light', '가로등', '기둥 + 팔 + 등기구'], ['roadx', '도로 디테일', '노면표시·신호등·전봇대'], ['slope', '경사 매스', '계단 위 쐐기 곡면'],
  ['detail', '건물 디테일', '지붕·옥탑·파라펫'], ['laser', '판 분리', '쌓는 순서대로']
];
// 2D 작업 칩: [단계, 이름, 이어지는 3D 줄]
const N2 = [
  ['0.5', '범위 상자', 'base'], ['1.1', '등고선 +200m', 'c3d'], ['2.2', 'TIN 보간', 'tin'], ['2.3', '0.5m 격자', 'tin'],
  ['2.5', '"이 높이 이상" 영역', 'sheet'], ['2.7', '윗판 ⊂ 아랫판', 'sheet'], ['3.2', '놓일 판 찾기', 'mass'], ['3.3', '층수 × 높이', 'mass'],
  ['4.4', '차도 끼움', 'inlay'], ['4.5', '곡면 위 길 층', 'path'], ['5.1', '가로수 대장', 'tree'], ['5.2', '항공사진 보충', 'tree'], ['5.5', '녹지 육각 격자', 'tree'],
  ['6.1', '경사 깎기', 'slope'], ['6.2', '건물 터', 'slope'], ['7.2', '경사지붕', 'detail'], ['7.5', '옥탑', 'detail'],
  ['9.3', '다음 판 자리 각인', 'laser'], ['9.5', '파일 쓰기', 'laser']
];
const rowOf = (key) => (key.startsWith('sheet_') ? 'sheet' : key);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

export function createLive(el) {
  el.innerHTML = `
  <div class="lv-head" hidden><span data-k="hid"></span><b data-k="hlabel"></b><em data-k="hx"></em></div>
  <div class="lv-body">
    <section class="lv-list" aria-label="2D 작업과 3D 부품">
      <div class="lv-lh"><span>2D 작업 → 3D 부품</span><span data-k="hcount">0/${N3.length}</span></div>
      <ul data-k="rows"></ul>
    </section>
    <div class="lv-side">
      <section class="lv-3d" aria-label="3D 지금"><div class="lv-gl" data-k="gl"></div>
        <div class="lv-chip"><small>3D 지금</small><b data-k="flabel">아직 없음</b></div>
        <div class="lv-hint">끌어서 돌리기 · 휠로 확대</div></section>
      <section class="lv-2d" aria-label="2D 지금">
        <div class="lv-plan"><img data-k="plan" alt="지금 단계의 평면 그림" hidden></div>
        <div class="lv-text"><small>2D 지금 <span data-k="sid"></span><span data-k="plabel" class="lv-pl"></span></small>
          <b data-k="slabel">모델 만들기를 누르면 시작해요</b><div class="lv-x" data-k="sx"></div>
          <p data-k="what"></p><p class="lv-why" data-k="why"></p></div>
      </section>
    </div>
  </div>`;
  const $ = (k) => el.querySelector(`[data-k="${k}"]`);

  // ---------------------------------------------------------------- 3D
  const gl = $('gl');
  const R = new THREE.WebGLRenderer({antialias: true, preserveDrawingBuffer: true});
  R.setPixelRatio(Math.min(devicePixelRatio, 2));
  R.toneMapping = THREE.ACESFilmicToneMapping; R.toneMappingExposure = 0.85;
  gl.appendChild(R.domElement);
  const S = new THREE.Scene(); S.background = new THREE.Color('#e9e9e9');
  S.add(new THREE.HemisphereLight(0xffffff, 0x8f8f8f, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6); S.add(sun, sun.target);
  const C = new THREE.PerspectiveCamera(30, 1, 1, 50000);
  const O = new OrbitControls(C, R.domElement);
  O.autoRotate = true; O.autoRotateSpeed = 0.6; O.enableDamping = true;
  O.addEventListener('start', () => { O.autoRotate = false; });
  R.domElement.addEventListener('wheel', () => { O.autoRotate = false; });
  const Z = smartZoom(C, O, R.domElement, () => root, null);   // 휠 = 마우스 아래 면으로 확대(모델 속으로 빠지지 않게)
  const root = new THREE.Group(); root.rotation.x = -Math.PI / 2; S.add(root);
  const loader = new GLTFLoader();
  const ORANGE = new THREE.Color('#3b82f6');
  let objs = {}, newest = null, fitted = false, exploded = false, gap = 10;

  function size() {
    const w = gl.clientWidth, h = gl.clientHeight;
    if (!w || !h) return;
    R.setSize(w, h, false); C.aspect = w / h; C.updateProjectionMatrix();
  }
  new ResizeObserver(size).observe(gl);
  R.setAnimationLoop(() => { if (gl.offsetParent) { O.update(); R.render(S, C); } });

  function fit(obj) {
    const b = new THREE.Box3().setFromObject(obj), c = b.getCenter(new THREE.Vector3());
    const d = Math.max(...b.getSize(new THREE.Vector3()).toArray());
    const az = THREE.MathUtils.degToRad(-35), el_ = THREE.MathUtils.degToRad(38), k = d * 2.15;
    C.position.set(c.x + k * Math.cos(el_) * Math.sin(az), c.y + d * 0.15 + k * Math.sin(el_), c.z + k * Math.cos(el_) * Math.cos(az));
    O.target.set(c.x, c.y + d * 0.08, c.z); C.near = d / 200; C.far = d * 40; C.updateProjectionMatrix(); O.update();
    sun.position.set(c.x + d * 0.7, c.y + d * 0.9, c.z + d * 0.3); sun.target.position.copy(c);
  }
  function mark(obj, on) {
    obj.traverse((o) => { if (o.isMesh) { o.material.emissive.copy(on ? ORANGE : new THREE.Color(0)); o.material.emissiveIntensity = on ? 0.4 : 0; } });
  }
  function drop(pattern) {
    for (const k of Object.keys(objs)) {
      if (pattern.endsWith('*') ? k.startsWith(pattern.slice(0, -1)) : k === pattern) { root.remove(objs[k]); delete objs[k]; }
    }
  }
  function loadPart(ev) {
    return new Promise((res) => loader.load(ev.url, (g) => {
      const obj = g.scene;
      obj.traverse((o) => {
        if (!o.isMesh) return;
        const a = o.geometry.attributes.color, vc = !!a;
        if (vc) { const t = new THREE.Color(); for (let i = 0; i < a.count; i++) { t.setRGB(a.getX(i), a.getY(i), a.getZ(i)).convertSRGBToLinear(); a.setXYZ(i, t.r, t.g, t.b); } }
        const soft = ev.key === 'tin' || ev.key === 'slope';
        if (soft) o.geometry.computeVertexNormals();
        o.material = new THREE.MeshStandardMaterial({color: vc ? 0xffffff : 0xc8c8c8, vertexColors: vc, roughness: 0.9,
          flatShading: !soft, side: ev.key === 'tin' ? THREE.DoubleSide : THREE.FrontSide,
          transparent: ev.key === 'tin', opacity: ev.key === 'tin' ? 0.85 : 1});
      });
      (ev.replaces || []).forEach(drop);
      drop(ev.key);
      objs[ev.key] = obj; root.add(obj);
      if (exploded) placeExploded();
      if (!fitted && ev.key === 'base') { fit(obj); Z.setModel(obj); fitted = true; gap = new THREE.Box3().setFromObject(obj).getSize(new THREE.Vector3()).x * 0.025; }
      if (newest && newest !== obj) mark(newest, false);
      mark(obj, true); newest = obj;
      res(obj);
    }, undefined, () => res(null)));
  }
  function placeExploded() {
    const keys = Object.keys(objs);
    for (const k of keys) {
      const o = objs[k];
      if (k.startsWith('sheet_')) { o.visible = true; o.position.z = exploded ? (+k.slice(6) + 1) * gap : 0; }
      else o.visible = !exploded || k === 'base';
    }
  }
  function snap() {
    R.render(S, C);
    const c = document.createElement('canvas'), w = 144, h = Math.round(144 * gl.clientHeight / Math.max(1, gl.clientWidth));
    c.width = w; c.height = h || 96;
    c.getContext('2d').drawImage(R.domElement, 0, 0, w, c.height);
    return c.toDataURL('image/jpeg', 0.8);
  }

  // ---------------------------------------------------------------- 상태
  let st;
  function reset() {
    for (const k of Object.keys(objs)) root.remove(objs[k]);
    objs = {}; newest = null; fitted = false; exploded = false; O.autoRotate = true;
    st = {cur: null, seen: new Set(), got: new Set(), last: null, thumbs: {}, sheetN: 0, sheetI: 0, finished: false, failed: false,
          planKey: '', planUrl: '', partInfo: {}, frame: ''};
    queue.length = 0;
    $('plan').hidden = true;
    render();
  }

  function render() {
    const cur = st.cur, fin = st.finished;
    const s3 = {};
    N3.forEach(([k]) => {
      s3[k] = st.got.has(k) ? (st.last === k && !fin ? 'active' : 'done') : fin ? 'skip' : 'todo';
    });
    const s2 = (id) => (cur && cur.id === id && !fin ? 'active' : st.seen.has(id) ? 'done' : fin ? 'skip' : 'todo');
    const rows = N3.map(([key, label, desc]) => {
      const chips = N2.filter((n) => n[2] === key).map((n) => ({id: n[0], label: n[1], state: s2(n[0])}));
      const es = chips.map((c) => (c.state === 'active' ? 'active' : c.state === 'done'
        ? (s3[key] === 'active' ? 'active' : s3[key] === 'todo' ? 'wait' : 'done') : 'todo'));
      const ls = es.includes('active') ? 'active' : es.includes('wait') ? 'wait' : es.includes('done') ? 'done' : 'todo';
      const z = s3[key];
      const pill = z === 'done' ? '생김' : z === 'active' ? '생기는 중' : z === 'skip' ? '없음' : ls === 'wait' ? '대기' : '아직';
      let lab = label;
      if (key === 'sheet' && st.got.has('sheet') && st.sheetN) lab += ` ${st.sheetI + 1}/${st.sheetN}`;
      const info = st.partInfo[key] || desc;
      const th = st.thumbs[key];
      const hot = z === 'active' || ls === 'active';
      const sub = chips.map((c) => `${c.id} ${c.label}`).join(' · ');
      const state = hot ? '만드는 중' : z === 'done' ? '완성' : z === 'skip' ? '없음' : '대기';
      const k = hot ? 'now' : z === 'done' ? 'done' : z === 'skip' ? 'skip' : 'todo';
      return `<li class="lv-r r-${k}"><span class="lv-dot"></span>
        <span class="lv-nm"><b>${esc(lab)}</b><small>${esc([sub, info].filter(Boolean).join(' · '))}</small></span>
        <span class="lv-st">${state}</span></li>`;
    }).join('');
    $('rows').innerHTML = rows;
    const n = N3.filter(([k]) => st.got.has(k)).length;
    $('hcount').textContent = `${n} / ${N3.length}`;
    const sub = cur ? SUB[cur.id] || [cur.id, '', ''] : null;
    if (fin) {
      $('hid').textContent = st.failed ? '멈춤' : '완료';
      $('hlabel').textContent = st.failed ? '만들지 못했습니다' : '모델 완성';
      $('hx').textContent = '';
      $('sid').textContent = ''; $('slabel').textContent = st.failed ? '중간에 멈췄습니다' : '모델 완성';
      $('sx').textContent = '';
      $('what').textContent = st.failed ? '왼쪽 칸이나 기록 탭에서 이유를 확인해 주세요.' : '모든 부품이 채워졌어요. 없음은 이번 설정에서 만들지 않는 부품이에요.';
      $('why').textContent = st.failed ? '' : '3D 보기 탭에서 최종 모델을 돌려 보거나, 결과 폴더를 열어 확인하세요.';
    } else if (sub) {
      $('hid').textContent = cur.id; $('hlabel').textContent = sub[0]; $('hx').textContent = cur.x || '';
      $('sid').textContent = cur.id; $('slabel').textContent = sub[0]; $('sx').textContent = cur.x || '';
      $('what').textContent = sub[1]; $('why').textContent = sub[2];
    }
    $('flabel').textContent = st.frame || '아직 없음';
    $('plabel').textContent = st.planKey ? ' · ' + st.planKey : '';
  }

  // ---------------------------------------------------------------- 이벤트 차례로 적용 (너무 빠른 단계도 눈에 보이게)
  const queue = [];
  let busy = false;
  const PLAN_LABEL = {c: '등고선', tin: '보간한 높이', b: '건물 발자국', r: '길 면', tr: '나무·녹지', sl: '경사 높이', fin: '완성 평면'};
  async function apply(ev) {
    if (ev.t === 'step') {
      st.cur = ev; st.seen.add(ev.id); if (ev.id === '2.5') st.sheetN = ev.n;
    } else if (ev.t === 'part') {
      const row = rowOf(ev.key);
      const obj = await loadPart(ev);
      if (!obj) return;
      st.got.add(row); st.last = row;
      if (row === 'sheet') { st.sheetI = +ev.key.slice(6); }
      if (ev.info) st.partInfo[row] = ev.info;
      st.frame = (N3.find((n) => n[0] === row) || [, row])[1] + (row === 'sheet' && st.sheetN ? ` ${st.sheetI + 1}/${st.sheetN}` : '');
      st.thumbs[row] = snap();
    } else if (ev.t === 'plan') {
      const img = $('plan');
      img.src = ev.url; img.hidden = false;
      st.planKey = ev.label ? ev.label : ev.key.startsWith('sheet_') ? `판 ${+ev.key.slice(6) + 1} 영역` : PLAN_LABEL[ev.key] || ev.key;
    } else if (ev.t === 'explode') {
      exploded = true; placeExploded();
      if (newest) mark(newest, false);
      st.got.add('laser'); st.last = 'laser'; st.frame = '판 분리 · 쌓는 순서';
      st.thumbs.laser = snap();
    } else if (ev.t === 'done') {
      exploded = false; placeExploded();
      if (newest) mark(newest, false);
      st.frame = '완성 모델';
    }
    render();
  }
  async function pump() {
    if (busy) return;
    busy = true;
    while (queue.length) {
      const ev = queue.shift();
      if (ev.t === 'sub') continue;          // 저장 세부 진행은 왼쪽 칸에서만 쓴다
      // 밀려 있으면 건너뛴다: 뒤에 같은 부품이 또 오면 앞의 것은 불러오지 않고, 연달은 단계 신호는 표시만 해 둔다
      const behind = queue.length > 6 || st.pendingFinish;
      if (ev.t === 'part' && queue.some((q) => q.t === 'part' && q.key === ev.key)) {
        st.got.add(rowOf(ev.key)); if (ev.info) st.partInfo[rowOf(ev.key)] = ev.info; continue;
      }
      if (behind && ev.t === 'step' && queue[0].t === 'step') { st.seen.add(ev.id); if (ev.id === '2.5') st.sheetN = ev.n; continue; }
      try { await apply(ev); } catch (e) { console.warn(e); }
      const wait = behind ? 30 : ev.t === 'step' ? 220 : 120;
      await new Promise((r) => setTimeout(r, wait));
    }
    busy = false;
    if (st.pendingFinish) { st.finished = true; st.failed = st.pendingFinish === 'err'; st.pendingFinish = null; render(); }
  }

  reset();
  return {
    reset,
    push(events) { if (events && events.length) { queue.push(...events); pump(); } },
    finish(ok) { st.pendingFinish = ok ? 'ok' : 'err'; if (!busy) pump(); },
    resize: size
  };
}
