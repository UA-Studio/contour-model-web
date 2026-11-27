// 결과: 평면·분석 그림, 완성 화면 파일 목록, 지난 결과 다시 열기
// 화면(index.html)이 ui_main.js 를 통해 불러옵니다. 다른 칸과 함께 쓰는 값은 ui_common.js 의 st 에 있습니다.
import {$, esc, st} from './ui_common.js';
import {rsubRender, rsubSync, show} from './ui_tabs.js';
import {mmss, mode} from './ui_run.js';
import {load3d} from './ui_view3d.js';
import {showErr} from './ui_errors.js';

// 결과 탭: 평면 미리보기 / 대지 분석 그림 / 3D / 기록
function planViews(an){
  rsubRender(an); st.resImg='preview_plan.png'; st.resultReady=true;
  $('plan').src='/result/preview_plan.png?t='+Date.now(); $('plan').alt='평면'; $('plan').hidden=false; $('planempty').hidden=true; rsubSync();
}
// 완성 화면 (v2.0): 파일을 쓰임새별로 묶고, 자주 여는 것을 맨 위에
const KIND={
  '.3dm':['render','Rhino 모델','3DM',1], '.skp':['render','스케치업 모델','SKP',1], '.dae':['render','스케치업에서 열기 (DAE)','DAE'],
  '.glb':['etc','미리보기 · 다른 3D 프로그램','GLB'], '.stl':['print','3D 프린트 (mm · 한 덩어리)','STL',1],
};
const NAMED2={'terrain_photo.obj':['render','항공사진 지형 (OBJ · 블렌더·키샷·트윈모션)','OBJ'],'terrain_photo.glb':['render','항공사진 지형 (GLB)','GLB'],'terrain_photo.jpg':['etc','항공사진 지형 텍스처 (OBJ 와 같은 폴더에)','JPG'],'terrain_photo.mtl':['etc','항공사진 지형 재질 (OBJ 와 같은 폴더에)','MTL'],'plan_2d.dxf':['plan','2D 지형도 (CAD · 등고선·건물·도로·지적)','DXF',1],'site_diagram.svg':['plan','사이트 다이어그램 (일러스트레이터 · 피그마)','SVG',1],
  'plan_2d_항공사진.jpg':['plan','2D 지형도에 붙는 항공사진 (DXF 와 같은 폴더에)','JPG'],'사용_안내.txt':['etc','파일별 단위·여는 법','TXT'],'section_data.npz':['etc','단면 만들기 자료 (지우지 마세요)','NPZ'],'분석_경사.png':['ana','경사 분석도','PNG'],'분석_절성토.png':['ana','절토·성토 지도','PNG'],'분석_요약.csv':['ana','대지 분석 숫자표 (엑셀)','CSV'],
  'preview_plan.png':['etc','평면 미리보기','PNG'],'road_marks_check.png':['etc','노면표시 확인 그림','PNG'],'street_trees.csv':['etc','가로수 목록','CSV'],
  'summary.json':['etc','모델 정보','JSON'],'model_sketchup_build.json':['etc','스케치업 만들기 자료 (확장 › 콘타 모델 만들기)','JSON'],
  'model_sketchup_info.json':['etc','스케치업 정보','JSON'],'스케치업_만들기.rb':['etc','스케치업 루비 스크립트','RB'],
  '스케치업_확장_설치파일(모델아님).rbz':['etc','스케치업 불러오기 확장 (모델 아님)','RBZ'],'스케치업에서_여는_법.txt':['etc','스케치업에서 여는 법','TXT']};
const GRP=[['render','렌더 · 3D'],['plan','2D 도면 · 다이어그램'],['laser','레이저 재단'],['print','3D 프린트'],['ana','대지 분석']];
let resDir='';
function mb(x){return x>1e6?`${(x/1e6).toFixed(1)} MB`:`${Math.max(1,Math.round(x/1e3))} KB`;}
async function doneView(j){
  let f={files:[],summary:{}}; try{f=await (await fetch('/files')).json();}catch(e){}
  const s=(j&&j.summary)||f.summary||{}; resDir=f.dir||'';
  $('dtime').textContent=s.seconds!=null?`${mmss(s.seconds)} 걸렸어요 · ${esc(f.name||'')}`:esc(f.name||'');
  const mm=s.model_size_mm||[];
  const facts=[s.sheets!=null?`판 ${s.sheets}장`:'',s.buildings!=null?`건물 ${s.buildings.toLocaleString()}동`:'',s.trees?`나무 ${s.trees.toLocaleString()}그루`:'',
    mm.length?`모형 ${mm[0]/10} × ${mm[1]/10} cm`:''].filter(Boolean);
  const an=s.analysis; const facts2=an?[`대지 ${an.site_area_m2.toLocaleString()}㎡`,`평균 경사 ${an.slope_mean_deg}°`,`${an.slope_limit_deg}° 이상 ${an.over_limit_pct}%`,
    ...(an.cut_m3!=null?[`깎기 ${an.cut_m3.toLocaleString()} · 쌓기 ${an.fill_m3.toLocaleString()} ㎥`]:[])]:[];
  $('dline').innerHTML=esc(facts.join(' · '))+(facts2.length?'<br>'+esc(facts2.join(' · ')):'');
  const notes=[...new Set($('plog').textContent.split('\n').map(x=>x.trim()).filter(x=>/못해|못했|못 받|건너|추정|없어 /.test(x)&&x.length<160))].slice(0,6);
  $('dnotes').hidden=!notes.length; $('dnotesn').textContent=notes.length;
  $('dnotesl').innerHTML=notes.map(x=>`<li>${esc(x)}</li>`).join('');
  const by={render:[],plan:[],laser:[],print:[],ana:[],etc:[]};
  const hasSkp=f.files.some(x=>/\.skp$/i.test(x.name));
  for(const x of f.files){
    if(x.dir){ if(x.name==='laser') by.laser.push({...x,label:`판 ${s.sheets??x.count}장 DXF`,ext:'DXF',pri:1,sub:`laser 폴더 · ${x.count}개 · ${mb(x.size)}`}); continue; }
    const ext=x.name.slice(x.name.lastIndexOf('.')).toLowerCase(); let k=NAMED2[x.name]||KIND[ext];
    if(ext==='.dae'&&hasSkp) k=['etc',KIND[ext][1],'DAE'];
    if(!k) k=['etc',x.name,ext.slice(1).toUpperCase()];
    by[k[0]].push({...x,label:k[1],ext:k[2],pri:k[3],sub:`${x.name} · ${mb(x.size)}`});
  }
  const card=x=>`<div class="fcard"><span class="ext">${esc(x.ext)}</span><span class="nm"><b>${esc(x.label)}</b><small>${esc(x.sub)}</small></span>`+
    `<button type="button" class="${x.pri?'pri':''}" data-f="${esc(x.name)}">${SRV&&!/^분석_.*\.png$|^preview_plan\.png$/.test(x.name)?'받기':x.dir?'폴더':/\.png$/i.test(x.name)?'보기':'열기'}</button></div>`;
  $('dgroups').innerHTML=GRP.filter(([k])=>by[k].length).map(([k,t])=>`<div class="fgrp"><p>${t}</p>${by[k].map(card).join('')}</div>`).join('')+
    (by.etc.length?`<details class="fmore"><summary>확인용 · 기타 파일 ${by.etc.length}개</summary>${by.etc.map(card).join('')}</details>`:'');
  $('dgroups').querySelectorAll('button[data-f]').forEach(b=>b.onclick=()=>{const n=b.dataset.f;
    if(/^분석_.*\.png$|^preview_plan\.png$/.test(n)){const i=st.RSUB.findIndex(r=>r[2]===n); if(i>=0){$('rsub').querySelector(`[data-i="${i}"]`).click(); return;}}
    if(SRV){location.href='/download?name='+encodeURIComponent(n);return;}
    fetch('/open',{method:'POST',body:JSON.stringify({name:n})});});
  $('fpath').innerHTML=SRV?'결과는 1시간 동안 아무것도 안 하거나 새로 만들면 서버에서 지워져요. 필요한 것은 지금 받아 두세요.':resDir?`${esc(resDir.replace(/[\\/][^\\/]+$/,''))}${resDir.includes('\\')?'\\':'/'}<b>${esc(f.name)}</b> <button type="button" class="lk2" id="cpath">경로 복사</button>`:'';
  const cp=$('cpath'); if(cp) cp.onclick=async()=>{try{await navigator.clipboard.writeText(resDir); cp.textContent='복사했어요';}catch(e){cp.textContent='복사 못 함';}};
  mode('done'); loadRecent(); show('p3d');
}
// 지난 결과: 창을 새로 고치거나 다른 날에도 다시 열어 볼 수 있게
async function loadRecent(){
  try{const j=await (await fetch('/recent')).json(); st.recentItems=j.items||[];}catch(e){st.recentItems=[];}
  const cur=resDir.split(/[\\/]/).pop();
  $('recent').innerHTML='최근 결과'+st.recentItems.slice(0,5).map(r=>`<button type="button" data-d="${esc(r.dir)}" class="${r.dir===cur?'on':''}">${esc(r.dir.replace(/_(\d{4})_(\d{4})$/,''))} · ${esc(r.when)}</button>`).join('·');
  const p=document.querySelector('.pane.on')?.id; $('recent').hidden=!(st.RES.includes(p)&&p!=='plog'&&st.recentItems.length);
  $('recent').querySelectorAll('button').forEach(b=>b.onclick=()=>openResult(b.dataset.d));
}
async function openResult(dir){
  const r=await fetch('/view',{method:'POST',body:JSON.stringify({dir})}); const j=await r.json();
  if(!r.ok){showErr({what:'그 결과를 열지 못했어요.',why:j.error,how:'결과 폴더를 옮겼거나 지웠다면 목록에서 빠져요.'});return;}
  const f=await (await fetch('/files')).json(); $('plog').textContent='';
  await doneView({summary:f.summary}); planViews((f.summary||{}).analysis); load3d(); show(st.lastRes==='plog'?'pplan':st.lastRes);
}

export {doneView, openResult, planViews};
