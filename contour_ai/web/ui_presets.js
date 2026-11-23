// 왼쪽 설정 칸: 용도 프리셋(레이저 · 렌더 · 빠르게), 지형/디테일 버튼, 인증키 서랍
// v1.4 왼쪽 칸: 용도 프리셋 · 지형/디테일 버튼 · 인증키 서랍
(function(){
const $=id=>document.getElementById(id);
const P={
  laser:{terrain:'step',detail:'1',on:{green:1,street:1,lights:0,roadx:0,structs:0,skp:0},d:'계단판 + 매스. 판마다 레이저 DXF, 나무 자리는 구멍으로'},
  render:{terrain:'combo',detail:'3',on:{green:1,street:1,lights:1,roadx:1,structs:1,skp:1},d:'경사 매스, 창문·재료, 나무·도로 디테일, 스케치업까지 전부'},
  quick:{terrain:'slope',detail:'1',on:{green:0,street:0,lights:0,roadx:0,structs:0,skp:0},d:'경사면 + 매스만. 범위가 맞는지 먼저 볼 때'},
  custom:{d:'아래 항목을 하나씩 켜고 끄세요'}};
const T=['green','street','lights','roadx','structs','skp'];
let cur='render', busy=false;
function store(k,v){try{localStorage.setItem('contour_'+k,v)}catch(e){}}
function load(k){try{return localStorage.getItem('contour_'+k)}catch(e){return null}}
function segSync(id,sel){document.querySelectorAll('#'+id+' button').forEach(b=>b.classList.toggle('on',b.dataset.v===$(sel).value));}
function sync(){
  document.querySelectorAll('#presets button').forEach(b=>{const on=b.dataset.p===cur;b.classList.toggle('on',on);b.setAttribute('aria-checked',on)});
  $('pdesc').textContent=P[cur].d;
  segSync('terrainseg','terrain'); segSync('detailseg','detail');
  const lowDetail=+$('detail').value<2; $('roadx').disabled=lowDetail;
  T.forEach(k=>$(k).closest('li').classList.toggle('off',!$(k).checked||$(k).disabled));
  $('aerialbox').hidden=false; $('lightbox').hidden=!$('lights').checked; $('roadxbox').hidden=!$('roadx').checked||lowDetail;
  const o=['3dm']; if($('terrain').value!=='slope') o.push('레이저 판 DXF'); if($('skp').checked) o.push('스케치업');
  $('outs').textContent=o.join(' · ');
  const sc=+$('scale').value||1000, iv=+$('interval').value||5;
  $('moresum').textContent=`1:${sc} · 판 ${iv} m · 층 ${$('floor').value} m`;
  const n=['afile','lfile','rfile','hddir','demfile'].filter(k=>$(k).value.trim()).length; $('filesum').textContent=n?`${n}개`:'없음';
}
function apply(p){cur=p; store('preset',p); const c=P[p];
  if(c.terrain){busy=true; $('terrain').value=c.terrain; $('detail').value=c.detail; T.forEach(k=>{$(k).checked=!!c.on[k]; $(k).dispatchEvent(new Event('change'))}); busy=false;}
  sync();}
document.querySelectorAll('#presets button').forEach(b=>b.onclick=()=>apply(b.dataset.p));
// 지형 다듬기 (v1.15): 부드럽게 / 등고선 그대로 — 이 PC에 기억
(()=>{const bs=[...document.querySelectorAll('#fitseg button')]; let v='smooth'; try{v=localStorage.getItem('contour_fit')||'smooth';}catch(e){}
  const set=x=>{bs.forEach(b=>b.classList.toggle('on',b.dataset.v===x)); try{localStorage.setItem('contour_fit',x);}catch(e){}};
  set(v); bs.forEach(b=>b.onclick=()=>set(b.dataset.v));})();
[['terrainseg','terrain'],['detailseg','detail']].forEach(([s,sel])=>document.querySelectorAll('#'+s+' button').forEach(b=>b.onclick=()=>{$(sel).value=b.dataset.v; cur='custom'; store('preset',cur); sync();}));
T.forEach(k=>$(k).addEventListener('change',()=>{if(!busy){cur='custom'; store('preset',cur);} sync();}));
['scale','interval','floor','afile','lfile','rfile','hddir'].forEach(k=>$(k).addEventListener('input',sync));
const saved=load('preset'); apply(P[saved]&&saved!=='custom'?saved:'render');

// 1단계: 다 넣으면 접기
window.markLoaded=()=>{$('s1').classList.add('done');};

// 인증키 서랍
const K=['vkey','dkey','skey','smkey'];
function keyState(){let n=0; K.forEach(k=>{const v=$(k).value.trim(); if(v) n++; const b=document.querySelector(`.kb[data-k="${k}"]`); b.textContent=v?'저장됨':'비어 있음'; b.classList.toggle('ok',!!v);});
  $('keycount').textContent=`${n}/4`; $('keybtn').classList.toggle('miss',!$('vkey').value.trim());}
K.forEach(k=>$(k).addEventListener('input',keyState)); keyState();
let lastFocus=null;
function openKeys(){lastFocus=document.activeElement; $('keys').hidden=false; $('scrim').hidden=false; $('vkey').focus();}
function closeKeys(){$('keys').hidden=true; $('scrim').hidden=true; keyState(); lastFocus&&lastFocus.focus();}
$('keybtn').onclick=openKeys; $('keyclose').onclick=closeKeys; $('scrim').onclick=closeKeys;
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('keys').hidden) closeKeys();});
window.openKeys=openKeys;
})();
