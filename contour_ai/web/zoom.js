// 3D 보기 휠 확대 (v1.10)
//
// 원래 OrbitControls 의 휠 확대는 '회전 중심(target)'까지 거리의 일정 비율로 다가간다. 회전 중심은 모델 상자의 한가운데
// (대개 허공이나 땅·건물 속)라서, 휠을 몇 번 굴리면 카메라가 그 점에 0.1m 까지 붙어 화면에 아무것도 안 보이고
// (모델이 사라진 것처럼 보임), 다시 멀어지려면 휠을 수십 번 굴려야 했다. 회전 중심이 땅 속이면 카메라가 지형 안으로
// 들어가 면이 모두 뒤쪽을 보게 되어 바로 빈 화면이 된다.
//
// 여기서는 휠을 굴리면 '마우스 아래에 있는 면'을 향해 다가간다. 그 면까지 남은 거리의 일정 비율만 움직이고,
// 면에서 최소 거리(모델 크기의 3%, 300m 모형이면 9m) 안으로는 들어가지 않는다. 멀어질 때도 모델 크기의 4배까지만.
// 두 번 누르기(더블클릭): 모델 위 = 그 점을 회전 중심으로, 빈 곳 = 처음 보기로.
import * as THREE from 'three';

export function smartZoom(C, O, dom, getRoot, onChange) {
  O.enableZoom = false;
  const rc = new THREE.Raycaster(), ndc = new THREE.Vector2();
  let size = 100, home = null, lastHit = null, lastT = 0, lastXY = [0, 0];

  function setModel(obj) {
    const b = new THREE.Box3().setFromObject(obj);
    if (b.isEmpty()) return;
    size = Math.max(...b.getSize(new THREE.Vector3()).toArray());
    home = {cam: C.position.clone(), tgt: O.target.clone(), center: b.getCenter(new THREE.Vector3())};
  }
  function pick(ev) {
    const r = dom.getBoundingClientRect();
    ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    rc.setFromCamera(ndc, C);
    const root = getRoot();
    if (!root) return null;
    const hits = rc.intersectObject(root, true).filter((h) => h.object.visible !== false && h.object.material && h.object.material.opacity > 0.5);
    return hits.length ? hits[0].point : null;
  }
  dom.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    if (!home) { const r = getRoot(); if (r) setModel(r); }
    const now = performance.now();
    // 휠을 연달아 굴리는 동안은 같은 자리를 다시 재지 않음(큰 모델에서 버벅임 방지)
    let hit = lastHit;
    if (!(now - lastT < 180 && Math.abs(ev.clientX - lastXY[0]) < 4 && Math.abs(ev.clientY - lastXY[1]) < 4)) hit = pick(ev);
    else rc.setFromCamera(ndc, C);
    lastT = now; lastXY = [ev.clientX, ev.clientY]; lastHit = hit;
    const dir = rc.ray.direction.clone();
    const dist = hit ? C.position.distanceTo(hit) : C.position.distanceTo(O.target);
    const minD = Math.max(size * 0.03, 2);
    const k = Math.min(Math.abs(ev.deltaY) / 100, 3) * 0.18;
    let step;
    if (ev.deltaY < 0) step = Math.min(dist * k, Math.max(dist - minD, 0));     // 다가가기: 면 앞 minD 에서 멈춤
    else {
      step = -dist * k / (1 - Math.min(k, 0.6));                                   // 멀어지기
      const c = home ? home.center : O.target;
      const far = size * 4, now_ = C.position.distanceTo(c);
      if (now_ - step > far) step = -Math.max(far - now_, 0);
    }
    if (Math.abs(step) < 1e-4) return;
    const mv = dir.multiplyScalar(step);
    C.position.add(mv); O.target.add(mv);                                           // 같이 옮겨서 보는 방향은 그대로
    // 회전 중심을 보는 방향 그대로 두되, 깊이는 마우스 아래 면 깊이로(멀리 땅속 점을 축으로 돌지 않게)
    const fwd = C.getWorldDirection(new THREE.Vector3());
    if (hit) {
      const depth = hit.clone().sub(C.position).dot(fwd);
      if (depth > minD) O.target.copy(C.position).addScaledVector(fwd, depth);
    }
    if (O.target.clone().sub(C.position).dot(fwd) < minD) O.target.copy(C.position).addScaledVector(fwd, minD * 4);
    C.near = Math.max(Math.min(size / 500, dist / 50), 0.02); C.updateProjectionMatrix();
    O.update(); onChange && onChange();
  }, {passive: false});
  dom.addEventListener('dblclick', (ev) => {
    const p = pick(ev);
    if (p) { O.target.copy(p); }
    else if (home) { C.position.copy(home.cam); O.target.copy(home.tgt); }
    C.near = Math.max(size / 500, 0.02); C.updateProjectionMatrix();
    O.update(); onChange && onChange();
  });
  return {setModel};
}
