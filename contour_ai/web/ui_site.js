// 대지: 법규 검토, 대지 경계(파일 · 지도에 그리기 · 주소), 대지 파일 읽기
// 화면(index.html)이 ui_main.js 를 통해 불러옵니다. 다른 칸과 함께 쓰는 값은 ui_common.js 의 st 에 있습니다.
import {$, esc, st} from './ui_common.js';
import {drawSite} from './ui_prep.js';
import {show} from './ui_tabs.js';
import {addFiles, renderList} from './ui_files.js';
import {drawSq, rangeView, texts} from './ui_map.js';

// 5. 대지 법규 검토

$('lawbtn').onclick=async()=>{
  const addr=$('saddr').value.trim(); if(!addr){$('lawres').textContent='주소를 넣어 주세요.';return;}
  $('lawres').textContent='토지이용계획 받는 중… (5~20초)';
  const r=await fetch('/landlaw',{method:'POST',body:JSON.stringify({address:addr, source:st.src&&st.src.id,
    vworld_key:($('vkey').value||$('vkey2').value).trim(), vworld_domain:$('vdomain').value.trim()})});
  const j=await r.json();
  if(!r.ok){$('lawres').innerHTML=`<span style="color:var(--err)">${esc(j.error)}</span>`;return;}
  const v=j.review; st.lawRing=j.rings||[j.ring];
  if(!st.site||st.site.kind==='addr') setSite({kind:'addr',rings:st.lawRing,xy:j.xy,area:j.area,bounds:j.bounds,in_sheet:j.in_sheet,label:v.address},false);
  $('lawres').innerHTML=`<b>${esc(v.address)}</b><br>${esc(v.zone||'용도지역 미상')}${v.bcr?` · 건폐율 ${v.bcr}% · 용적률 ${v.far}%`:''} · 확인할 것 ${v.checks.length}가지`+
    (j.in_sheet===false?`<br><span style="color:var(--err)">넣은 도엽 밖의 대지입니다. 그 도엽 zip도 함께 넣어 주세요.</span>`:'');
  $('lawempty').hidden=true;
  $('lawbody').innerHTML=`<h2 style="margin:0 0 4px">${esc(v.address)}</h2><div style="color:var(--sub);margin-bottom:14px">PNU ${esc(v.pnu)} · ${esc(v.jibun)}</div>
   <table class="lawt"><tr><th>항목</th><th>내용</th><th>근거</th></tr>${v.rows.map(r=>`<tr><td>${esc(r[0])}</td><td><b>${esc(r[1])}</b></td><td>${esc(r[2])}</td></tr>`).join('')}</table>
   <h3>토지이용계획</h3><table class="lawt"><tr><th>지역·지구·구역</th><th>저촉</th><th>메모</th></tr>${v.land_use.map(u=>`<tr><td>${esc(u.name)}</td><td>${esc(u.conflict)}</td><td>${esc(u.note)}</td></tr>`).join('')}</table>
   ${v.checks.length?`<h3>확인할 것</h3><ul>${v.checks.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>`:''}
   <p style="color:var(--sub);font-size:12px">${esc(v.note)}</p>`;
  drawSite(); show('plaw');
};
// 대지 경계: 파일 · 지도에 그리기 · 주소(법규 검토) 가운데 마지막에 넣은 것

function setSite(v,go=true){
  st.site=v; st.drawing=null; $('mapbox').classList.remove('draw'); $('drawtip').hidden=true; $('sitedrawbtn').textContent='지도에 그리기';
  $('siteclr').hidden=!v; $('siteres').textContent='';
  if(!v){$('sitesum').textContent='선택'; drawSite(); renderList(); rangeView(); return;}
  $('sitesum').textContent=`${Math.round(v.area).toLocaleString()}㎡`+($('siteclear').checked?' · 비움':'');
  if(v.standard) $('siteres').innerHTML=`<span style="color:var(--sub)">표준 대지 파일${v.saved?' · '+esc(v.saved):''}</span>`;
  // 도엽 밖으로 나가는 대지도 범위는 대지 쪽으로 옮긴다 (범위는 도엽 안으로 밀려 들어간다)
  if(st.src&&v.xy){st.center=v.xy; $('place').value='';
    // 대지가 범위보다 크면 범위를 대지 + 여유 50m 로 키운다
    if(v.bounds){const need=[v.bounds[2]-v.bounds[0]+100, v.bounds[3]-v.bounds[1]+100];
      if(need[0]>+$('w').value) $('w').value=Math.ceil(need[0]/50)*50;
      if(need[1]>+$('h').value) $('h').value=Math.ceil(need[1]/50)*50; texts();}
    drawSq();}
  st.rangeEdit=false; st.centerMoved=false; drawSite(); renderList(); rangeView(); if(go) show('pmap');
}
$('siteclear').addEventListener('change',()=>{if(st.site) $('sitesum').textContent=`${Math.round(st.site.area).toLocaleString()}㎡`+($('siteclear').checked?' · 비움':'');});
$('siteclr').onclick=()=>setSite(null);
$('sitefilebtn').onclick=()=>$('sitefile').click();
// 대지 파일 읽기: 어떤 실패든 '읽는 중'에 멈춰 있지 않고 이유와 해결 방법을 띄운다
function siteErr(msg){$('siteres').innerHTML=`<span style="color:var(--err)">${esc(msg)}</span>`;}
$('sitefile').onchange=e=>{const f=e.target.files[0]; e.target.value=''; if(f) addFiles([f]);};
async function siteUpload(f){
  st.pendingSite=f; renderList(); $('siteres').textContent='';
  const ctl=new AbortController(), tm=setTimeout(()=>ctl.abort(),180000);
  let r, txt='';
  const done=()=>{st.pendingSite=null; renderList();};
  try{
    r=await fetch('/siteupload',{method:'POST',headers:{'X-Filename':encodeURIComponent(f.name),'X-Source':encodeURIComponent(st.src.id)},body:f,signal:ctl.signal});
    txt=await r.text();
  }catch(err){
    clearTimeout(tm); done();
    siteErr(err.name==='AbortError'?`${f.name}: 3분이 지나도 읽기가 끝나지 않아 멈췄습니다. 도면이 아주 크면 대지 경계·레벨 레이어만 남겨 DXF로 다시 저장해 넣어 주세요.`
      :/Failed to fetch|NetworkError|Load failed/i.test(err.message)?`${f.name}: 프로그램에 닿지 못했습니다. 검은 창(프로그램)이 켜져 있는지 확인하고 다시 넣어 주세요. (${err.message})`
      :`${f.name}: 화면이 파일을 보내기 전에 오류가 났습니다(프로그램은 정상). 이 화면 그림을 보내 주시면 고치겠습니다. (${err.message})`); return;}
  clearTimeout(tm); done();
  let j; try{j=JSON.parse(txt);}catch(_){siteErr(`${f.name}: 읽는 중에 문제가 생겼습니다. 진행 기록이나 검은 창의 내용을 알려 주세요. (${txt.slice(0,160)})`);return;}
  if(!r.ok){siteErr(j.error||'경계 파일을 읽지 못했습니다.');
    if(j.prep&&j.path) $('siteres').insertAdjacentHTML('beforeend',`<br><button class="ghost" type="button" data-prep="${esc(j.path)}" style="margin-top:6px;padding:6px 12px;font-size:12px">레이어를 직접 골라 정리하기</button>`);
    return;}
  try{setSite({...j,kind:'file',label:f.name});}catch(err){siteErr(`${f.name}: 읽었지만 화면에 그리지 못했습니다. (${err.message})`);}
}

export {setSite, siteErr, siteUpload};
