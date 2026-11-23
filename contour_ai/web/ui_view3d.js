// 3D 보기 (three.js)
// 화면(index.html)이 ui_main.js 를 통해 불러옵니다. 다른 칸과 함께 쓰는 값은 ui_common.js 의 st 에 있습니다.
import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {smartZoom} from './zoom.js';
import {$} from './ui_common.js';
import {initSun} from './ui_sun.js';

// 3D 보기 (결과가 이미 있으면 window.load3d() 로 다시 불러올 수 있음)

let R,S,C,O,Z,root,sun,MATS={},loadSeq=0;
function init3d(){
  R=new THREE.WebGLRenderer({antialias:true,powerPreference:'high-performance',preserveDrawingBuffer:true}); R.setPixelRatio(Math.min(devicePixelRatio,1.5));
  R.shadowMap.enabled=true; R.shadowMap.type=THREE.PCFSoftShadowMap; R.toneMapping=THREE.ACESFilmicToneMapping; R.toneMappingExposure=0.8;
  $('three').appendChild(R.domElement);
  S=new THREE.Scene(); S.background=new THREE.Color('#d9d9d9');
  S.add(new THREE.HemisphereLight(0xffffff,0x9a9a9a,1.5));
  C=new THREE.PerspectiveCamera(30,1,1,20000); O=new OrbitControls(C,R.domElement); O.addEventListener('change',draw);
  Z=smartZoom(C,O,R.domElement,()=>root,draw);   // 휠 = 마우스 아래 면으로 확대(모델 속으로 빠지지 않게)
}
function draw(){R&&R.render(S,C);}
// 3D 보기 레이어 켜고 끄기 (v2.2): 지형·건물·길·나무·가로등·도로 표시·대지 경계를 하나씩.
// GLB 안 개체 이름(out_stl.py 가 붙임)으로 나눈다. 숲이 넓으면 나무가 지형·건물을 덮어 끄고 볼 수 있게 한 것이 시작.
const LAYERS=[['terrain','지형',/^terrain/],['buildings','건물',/^buildings/],['paths','길',/^path_|^inlay_/],
  ['trees','나무',/^t\d+$|^trees$|^tree_/],['lights','가로등',/^streetlights/],['marks','도로 표시',/^road_detail_/],['site','대지 경계',/^site_boundary/]];
let GROUPS={}, TREES=[];
function layerOf(o){for(let q=o;q&&q!==root;q=q.parent){const L=LAYERS.find(l=>l[2].test(q.name)); if(L) return L[0];} return null;}
function buildToggles(){
  GROUPS={}; LAYERS.forEach(l=>GROUPS[l[0]]=[]);
  root.traverse(o=>{if(!o.isMesh) return; const k=layerOf(o); if(k) GROUPS[k].push(o);});
  TREES=GROUPS.trees;
  const box=$('treetg'); box.innerHTML='';
  LAYERS.forEach(([k,label])=>{ if(!GROUPS[k].length) return;
    const b=document.createElement('button'); b.type='button'; b.textContent=label; b.dataset.k=k; b.setAttribute('aria-pressed','true');
    b.onclick=()=>{const on=b.classList.toggle('off'); GROUPS[k].forEach(o=>o.visible=!on); b.setAttribute('aria-pressed',String(!on)); if(sun) sun.shadow.needsUpdate=true; draw();};
    box.appendChild(b);});
  box.hidden=!box.children.length;
}
function resize3d(){if(!R) return; const el=$('three'); R.setSize(el.clientWidth,el.clientHeight); C.aspect=el.clientWidth/el.clientHeight; C.updateProjectionMatrix(); draw();}
window.load3d=()=>load3d();
function load3d(){
  if(!R) init3d(); $('threeempty').textContent='3D 모델 불러오는 중…';
  if(root){S.remove(root); root.traverse(o=>{if(o.isMesh) o.geometry.dispose();}); root=null;}
  MATS={}; const my=++loadSeq;
  new GLTFLoader().load('/result/model.glb?t='+Date.now(),g=>{
    // 불러오기가 겹치면(완성 직후 + 3D 탭) 늦게 끝난 옛 것은 버린다. 두 벌이 겹쳐 쌓이면 레이어 끄기가 안 먹었다
    if(my!==loadSeq) return;
    if(root){S.remove(root); root.traverse(o=>{if(o.isMesh) o.geometry.dispose();});}
    root=g.scene; root.rotation.x=-Math.PI/2;
    root.traverse(o=>{if(!o.isMesh) return; o.castShadow=o.receiveShadow=true; const vc=!!o.geometry.attributes.color, soft=o.name.includes('terrain_slope')||o.name==='terrain';
      if(soft) o.geometry.computeVertexNormals();
      // 색 변환은 같은 메시를 쓰는 개체가 여럿이어도 한 번만. 나무는 변환하지 않아 밝게(변환하면 숲이 검게 보였다)
      let tree=false; for(let q=o;q;q=q.parent) if(/^t\d+$|^trees$|^tree_/.test(q.name)){tree=true;break;}
      if(vc&&!o.geometry.userData.lin&&!tree){o.geometry.userData.lin=1; const a=o.geometry.attributes.color, t=new THREE.Color(); for(let i=0;i<a.count;i++){t.setRGB(a.getX(i),a.getY(i),a.getZ(i)).convertSRGBToLinear(); a.setXYZ(i,t.r,t.g,t.b);}}
      // 재질은 종류별로 하나만 만들어 함께 쓴다 (부품 수천 개가 각자 재질을 가지면 화면이 버벅였다)
      const mk=(vc?1:0)+(soft?2:0); o.material=MATS[mk]||(MATS[mk]=new THREE.MeshStandardMaterial({color:vc?0xffffff:0xc8c8c8,vertexColors:vc,roughness:0.9,flatShading:!soft}));});
    S.add(root);
    const b=new THREE.Box3().setFromObject(root), c=b.getCenter(new THREE.Vector3()), d=Math.max(...b.getSize(new THREE.Vector3()).toArray());
    if(sun){S.remove(sun,sun.target);}
    sun=new THREE.DirectionalLight(0xffffff,1.8); sun.castShadow=true; sun.shadow.mapSize.set(2048,2048); sun.shadow.autoUpdate=false; sun.shadow.needsUpdate=true; sun.shadow.bias=-0.0004; sun.shadow.normalBias=0.4;
    sun.position.set(c.x+d*0.75,c.y+d*0.7,c.z-d*0.25); sun.target.position.copy(c); S.add(sun,sun.target);
    const sc=sun.shadow.camera; sc.left=sc.bottom=-d*0.8; sc.right=sc.top=d*0.8; sc.near=1; sc.far=d*4; sc.updateProjectionMatrix();
    C.position.set(c.x-d*0.95,c.y+d*0.95,c.z+d*1.15); O.target.copy(c); O.update(); C.near=Math.max(d/500,0.05); C.far=d*20; C.updateProjectionMatrix(); Z.setModel(root);
    buildToggles();
    initSun({R,S,C,sun,root,center:c,d,draw});
    $('threeempty').hidden=true; $('legend').hidden=false; resize3d();
  },undefined,e=>{$('threeempty').textContent='3D 미리보기를 열지 못했습니다. 결과 폴더의 model.3dm을 Rhino로 열어 보세요.';});
}

export {load3d, resize3d};
