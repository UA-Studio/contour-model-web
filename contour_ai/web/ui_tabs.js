// 위 탭(지도 · 진행 · 결과 · 법규)과 결과 탭 안의 작은 탭
// 화면(index.html)이 ui_main.js 를 통해 불러옵니다. 다른 칸과 함께 쓰는 값은 ui_common.js 의 st 에 있습니다.
import {createLive} from './live.js';
import {$, st} from './ui_common.js';
import {resize3d} from './ui_view3d.js';
import {secBarSync} from './ui_section.js';

st.live=createLive($('plive'));

// 탭
// v2.0: 위 탭은 지도 · 진행 · 결과 · 법규 검토. 결과 탭 안에서 평면 · 경사 · 절성토 · 3D · 기록을 고른다

document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>show(b.dataset.p));
function show(p){if(p==='res') p=st.lastRes; if(st.RES.includes(p)) st.lastRes=p;
  document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('on',x.dataset.p===p||(x.dataset.p==='res'&&st.RES.includes(p))));
  document.querySelectorAll('.pane').forEach(x=>x.classList.toggle('on',x.id===p));
  $('rsub').hidden=!st.RES.includes(p); $('recent').hidden=!(st.RES.includes(p)&&p!=='plog'&&st.recentItems.length);
  $('zoomui').hidden=!(p==='pmap'&&st.src); secBarSync(p); rsubSync(); if(p==='p3d') resize3d();}

function rsubRender(an){
  st.RSUB=[['평면','pplan','preview_plan.png']];
  if(an&&an.files){ if(an.files.includes('분석_경사.png')) st.RSUB.push(['경사 분석','pplan','분석_경사.png']);
    if(an.files.includes('분석_절성토.png')) st.RSUB.push(['절토·성토','pplan','분석_절성토.png']); }
  st.RSUB.push(['3D','p3d'],['기록','plog']);
  $('rsub').innerHTML=st.RSUB.map((r,i)=>`<button type="button" role="tab" data-i="${i}">${r[0]}</button>`).join(''); rsubSync();}
function rsubSync(){const p=document.querySelector('.pane.on')?.id;
  $('rsub').querySelectorAll('button').forEach(b=>{const r=st.RSUB[+b.dataset.i]; const on=r[1]===p&&(r[1]!=='pplan'||r[2]===st.resImg); b.classList.toggle('on',on); b.setAttribute('aria-selected',on);});}
$('rsub').addEventListener('click',e=>{const b=e.target.closest('button'); if(!b) return; const r=st.RSUB[+b.dataset.i];
  if(r[1]==='pplan'&&r[2]){st.resImg=r[2]; if(!$('plan').hidden||st.resultReady){$('plan').src='/result/'+encodeURIComponent(r[2])+'?t='+Date.now(); $('plan').alt=r[0];}}
  show(r[1]);});

export {rsubRender, rsubSync, show};
