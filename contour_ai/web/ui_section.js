// 지형 단면 (v2.2): 모델을 만든 뒤 지도에 선 두 점을 찍으면 그 선의 단면(SVG·DXF)을 결과 폴더에 만든다.
// 보는 사람은 선의 오른쪽(A→A' 방향 기준)에 서서 왼쪽을 본다. '뒤쪽 깊이' 안의 지형·건물은 연하게 그린다.
import {$, esc, st} from './ui_common.js';
import {show, rsubSync} from './ui_tabs.js';
import {drawSite} from './ui_prep.js';

st.secLine=null;            // 그리는 중: [] → [[x,y]] → [[x,y],[x,y]]
export function secBarSync(p){ const b=$('secbar'); if(b) b.hidden=!(p==='pmap'&&st.src&&st.resultReady); }
$('secbtn').onclick=()=>{
  if(st.secLine&&st.secLine.length<2){st.secLine=null; $('secbtn').textContent='단면선 긋기'; $('secinfo').textContent=''; $('mapbox').classList.remove('draw'); drawSite(); return;}
  st.secLine=[]; $('secgo').disabled=true; $('mapbox').classList.add('draw'); $('secbtn').textContent='긋기 취소';
  $('secinfo').textContent='지도에서 단면 시작점(A)과 끝점(A\')을 차례로 누르세요.'; drawSite();
};
export function secClick(p){          // ui_map.js 가 지도 누름을 넘겨준다. 처리했으면 true
  if(!st.secLine||st.secLine.length>=2) return false;
  st.secLine.push([Math.round(p[0]*10)/10,Math.round(p[1]*10)/10]);
  if(st.secLine.length===2){
    const [a,b]=st.secLine, L=Math.hypot(b[0]-a[0],b[1]-a[1]);
    $('mapbox').classList.remove('draw'); $('secbtn').textContent='다시 긋기'; $('secgo').disabled=false;
    $('secinfo').textContent=`길이 ${Math.round(L)} m · 선의 왼쪽이 뒤쪽이에요`;
  } else $('secinfo').textContent='끝점(A\')을 누르세요.';
  drawSite(); return true;
}
$('secgo').onclick=async()=>{
  if(!st.secLine||st.secLine.length<2) return;
  const b=$('secgo'); b.disabled=true; b.textContent='만드는 중…';
  try{
    const r=await fetch('/section',{method:'POST',body:JSON.stringify({a:st.secLine[0],b:st.secLine[1],depth:+$('secdepth').value||0})});
    const j=await r.json(); if(!r.ok) throw new Error(j.error||r.status);
    const n=j.svg.match(/\d+/)[0]*1, label=`단면 ${n}`;
    st.RSUB.splice(st.RSUB.length-2,0,[label,'pplan',j.svg]);
    $('rsub').innerHTML=st.RSUB.map((r,i)=>`<button type="button" role="tab" data-i="${i}">${r[0]}</button>`).join('');
    st.resImg=j.svg; $('plan').src='/result/'+encodeURIComponent(j.svg)+'?t='+Date.now(); $('plan').alt=label; $('plan').hidden=false; $('planempty').hidden=true;
    show('pplan'); rsubSync();
    $('secinfo').innerHTML=`${esc(label)} 완성 · 잘린 건물 ${j.cut_buildings} · 뒤쪽 건물 ${j.back_buildings} · `+
      `<a href="/download?name=${encodeURIComponent(j.svg)}">SVG</a> · <a href="/download?name=${encodeURIComponent(j.dxf)}">DXF</a>`;
  }catch(e){$('secinfo').innerHTML=`<span style="color:var(--err)">${esc(String(e.message||e))}</span>`;}
  finally{b.disabled=false; b.textContent='단면 만들기';}
};
