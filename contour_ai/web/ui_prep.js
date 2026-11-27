// 대지 파일 정리 화면: 레이어마다 역할 고르기 → 표준 대지 파일, 수치지도 레이어 한글 이름
// 화면(index.html)이 ui_main.js 를 통해 불러옵니다. 다른 칸과 함께 쓰는 값은 ui_common.js 의 st 에 있습니다.
import {$, esc, st} from './ui_common.js';
import {setSite, siteErr} from './ui_site.js';
import {show} from './ui_tabs.js';

// ---- 대지 파일 정리 화면: 레이어마다 역할을 고르고 표준 대지 파일(SITE_ 레이어)로 저장
const PP={roles:['경계','레벨판','레벨점','계획등고선','원지형유지','건물','도로','무시'],an:null,path:null,mem:{}};
try{PP.mem=JSON.parse(localStorage.getItem('contourSiteRoles')||'{}')}catch(_){PP.mem={}}
$('s1').addEventListener('click',e=>{const b=e.target.closest('[data-prep]'); if(b){e.stopPropagation(); openPrep(b.dataset.prep);}});
$('ppclose').onclick=()=>$('prep').classList.remove('on');
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('prep').classList.contains('on')) $('prep').classList.remove('on');});
async function openPrep(path){
  $('siteres').insertAdjacentHTML('beforeend','<span id="ppwait"><br>레이어 읽는 중…</span>');
  let r,j; try{r=await fetch('/siteprep',{method:'POST',body:JSON.stringify({source:st.src.id,path})}); j=JSON.parse(await r.text());}
  catch(err){$('ppwait')?.remove(); siteErr(`레이어를 읽지 못했습니다. 검은 창(프로그램)이 켜져 있는지 확인해 주세요. (${err.message})`); return;}
  $('ppwait')?.remove();
  if(!r.ok){siteErr(j.error||'레이어를 읽지 못했습니다.'); return;}
  PP.an=j; PP.path=path;
  for(const L of j.layers){L.guess=L.role; if(PP.mem[L.name]&&PP.roles.includes(PP.mem[L.name])){L.role=PP.mem[L.name]; L.mem=L.role!==L.guess;}}
  $('pphow').textContent=`${j.file} · ${j.how}`+(j.standard?' · 이미 표준 대지 파일입니다':'');
  ppList(); ppDraw(); ppHL(null); $('prep').classList.add('on');
}
function ppSummary(L){const a=[];
  if(L.closed) a.push(`닫힌 선 ${L.closed}${L.closed_z?' (높이 '+L.closed_z+')':''}`);
  if(L.open) a.push(`선 ${L.open}${L.open_z?' (높이 '+L.open_z+')':''}`);
  if(L.levels) a.push(`레벨 숫자 ${L.levels}`+(L.values&&L.values.length?` (${L.values.slice(0,4).join(', ')}${L.values.length>4?' …':''})`:''));
  if(L.map_spots) a.push(`수치지도 표고점과 같은 숫자 ${L.map_spots}`);
  if(L.area>=1) a.push(`가장 큰 면 ${Math.round(L.area).toLocaleString()}㎡`);
  return a.join(' · ');}
// 수치지도 레이어 코드(1.0: A0013118, 2.0: N3A_B0010000) → 한글 이름 (앞 4자리: 분류+번호)
const MAPCAT={A:'교통',B:'건물',C:'시설',D:'식생',E:'수계',F:'지형',G:'경계',H:'주기'};
const MAPCODE={A001:'도로경계',A002:'도로중심선',A003:'보도',A004:'횡단보도',A005:'안전지대',A006:'육교',A007:'교량',A008:'교차로',A009:'입체교차부',A010:'인터체인지',A011:'터널',A012:'터널입구',A013:'정거장',A014:'철도',A015:'철도경계',A016:'철도중심선',
  B001:'건물',B002:'담장',F001:'등고선',F002:'표고점',F003:'절토·성토',F004:'옹벽',E001:'하천경계',E002:'하천중심선',G001:'행정경계',H001:'지명',H004:'주기'};
function mapCode(n){let m=String(n).match(/^N3[APLC]_([A-H])(\d{3})\d{4}$/)||String(n).match(/^([A-H])(\d{3})\d{4}$/);
  if(!m) return null; const c=m[1]+m[2]; return (MAPCODE[c]||MAPCAT[m[1]]+' '+m[2])+' · 수치지도';}
function ppList(){
  const used=PP.an.layers.filter(L=>L.role!=='무시'), rest=PP.an.layers.filter(L=>L.role==='무시');
  const restMap=rest.filter(L=>mapCode(L.name)), restOther=rest.filter(L=>!mapCode(L.name));
  const row=L=>{const kn=mapCode(L.name); return `<div class="pp-row r${PP.roles.indexOf(L.role)}" data-l="${esc(L.name)}"><div><b>${esc(L.name)}${kn?`<span class="kn">${esc(kn)}</span>`:''}</b>${L.mem?'<em>기억한 역할</em>':''}<small>${esc(ppSummary(L))}</small></div>`+
    `<select>${PP.roles.map(r=>`<option${r===L.role?' selected':''}>${r}</option>`).join('')}</select></div>`;};
  $('pplist').innerHTML=`<div class="pp-sec">쓰는 레이어 ${used.length}</div>`+used.map(row).join('')+
    (restOther.length?`<div class="pp-sec">무시하는 레이어 ${restOther.length}</div>`+restOther.map(row).join(''):'')+
    (restMap.length?`<details class="ppmap"><summary>수치지도 바탕 레이어 ${restMap.length}개 (건물·도로·등고선 등, 보통 무시)</summary>${restMap.map(row).join('')}</details>`:'');
}
$('pplist').addEventListener('change',e=>{const row=e.target.closest('.pp-row'); if(!row) return;
  const L=PP.an.layers.find(x=>x.name===row.dataset.l); L.role=e.target.value; L.mem=false;
  row.className=`pp-row r${PP.roles.indexOf(L.role)}`; const g=PP.g[L.name]; if(g) g.setAttribute('class',`r${PP.roles.indexOf(L.role)}`);});
$('pplist').addEventListener('mouseover',e=>{const row=e.target.closest('.pp-row'); ppHL(row?row.dataset.l:null);});
$('pplist').addEventListener('mouseleave',()=>ppHL(null));
function ppHL(name){for(const k in PP.g) PP.g[k].classList.toggle('hl',k===name);}
function ppDraw(){
  const an=PP.an, G=$('ppg'); G.innerHTML=''; PP.g={};
  // 보기 범위: 경계 역할 레이어(없으면 쓰는 레이어) 둘레 + 여유
  const pick=r=>an.geoms.filter(g=>(an.layers.find(L=>L.name===g.layer)||{}).role===r);
  let base=pick('경계'); if(!base.length) base=an.geoms.filter(g=>(an.layers.find(L=>L.name===g.layer)||{}).role!=='무시'); if(!base.length) base=an.geoms;
  let x0=1e20,y0=1e20,x1=-1e20,y1=-1e20; for(const g of base) for(const [x,y] of g.xy){x0=Math.min(x0,x);y0=Math.min(y0,y);x1=Math.max(x1,x);y1=Math.max(y1,y);}
  const m=Math.max(x1-x0,y1-y0)*0.25+20; x0-=m;y0-=m;x1+=m;y1+=m;
  $('ppsvg').setAttribute('viewBox',`${x0} ${-y1} ${x1-x0} ${y1-y0}`);
  const rad=Math.max(x1-x0,y1-y0)/260, by={};
  for(const g of an.geoms) (by[g.layer]=by[g.layer]||[]).push(g);
  // 큰 면이 먼저, 레벨 점이 나중에 그려지게
  const order=an.layers.slice().sort((a,b)=>(b.role==='무시')-(a.role==='무시')||b.area-a.area);
  for(const L of order){const gs=by[L.name]; if(!gs) continue;
    const g=document.createElementNS('http://www.w3.org/2000/svg','g'); g.setAttribute('class',`r${PP.roles.indexOf(L.role)}`);
    let d='', o='', c='';
    for(const q of gs){
      const inView=q.xy.some(([x,y])=>x>=x0&&x<=x1&&y>=y0&&y<=y1)||q.xy.length>1&&!(Math.max(...q.xy.map(p=>p[0]))<x0||Math.min(...q.xy.map(p=>p[0]))>x1||Math.max(...q.xy.map(p=>p[1]))<y0||Math.min(...q.xy.map(p=>p[1]))>y1);
      if(!inView) continue;
      if(q.kind==='level'){c+=`<circle cx="${q.xy[0][0]}" cy="${-q.xy[0][1]}" r="${rad}"><title>${esc(L.name)} · ${q.z}</title></circle>`; continue;}
      const seg='M'+q.xy.map(([x,y])=>`${x} ${-y}`).join('L');
      if(q.kind==='closed') d+=seg+'Z'; else o+=seg;}
    // 닫힌 선은 면으로, 열린 선은 선으로만 (열린 선을 칠하면 엉뚱한 삼각형이 생긴다)
    g.innerHTML=(d?`<path d="${d}" fill-rule="evenodd"><title>${esc(L.name)}</title></path>`:'')+
      (o?`<path d="${o}" style="fill:none"><title>${esc(L.name)}</title></path>`:'')+c;
    G.appendChild(g); PP.g[L.name]=g;}
}
$('ppsave').onclick=async()=>{
  const roles={}; for(const L of PP.an.layers) roles[L.name]=L.role;
  if(!Object.values(roles).includes('경계')){$('ppnote').innerHTML='<b style="color:var(--ink)">대지 경계가 그려진 레이어 하나를 \'경계\'로 골라 주세요.</b>'; return;}
  $('ppsave').disabled=true; $('ppsave').textContent='저장하는 중…';
  let r,j; try{r=await fetch('/sitesave',{method:'POST',body:JSON.stringify({source:st.src.id,path:PP.path,roles})}); j=JSON.parse(await r.text());}
  catch(err){j={error:`저장하지 못했습니다. 검은 창(프로그램)이 켜져 있는지 확인해 주세요. (${err.message})`}; r={ok:false};}
  $('ppsave').disabled=false; $('ppsave').textContent='표준 파일로 저장하고 쓰기';
  if(!r.ok){$('ppnote').innerHTML=`<b style="color:var(--ink)">${esc(j.error||'저장하지 못했습니다.')}</b>`; return;}
  for(const L of PP.an.layers){ if(L.role!==L.guess||PP.mem[L.name]) PP.mem[L.name]=L.role; }
  try{localStorage.setItem('contourSiteRoles',JSON.stringify(PP.mem))}catch(_){}
  $('prep').classList.remove('on');
  const c=j.info.counts;
  setSite({...j,kind:'file',label:j.saved.split(/[\\/]/).pop()});
  $('siteres').insertAdjacentHTML('beforeend',`<br><span style="color:var(--sub)">레벨판 ${c['레벨판']} · 레벨점 ${c['레벨점']} · 계획 등고선 ${c['계획등고선']} · 원지형 유지 ${c['원지형유지']} · 건물 ${c['건물']} · 도로 ${c['도로']}`+
    (j.notes&&j.notes.length>1?'<br>'+j.notes.slice(0,-1).map(esc).join('<br>'):'')+'</span>');
};
$('sitedrawbtn').onclick=()=>{
  if(!st.src){$('siteres').textContent='먼저 01번 칸에 수치지도 zip을 넣어 주세요.';return;}
  if(st.drawing){finishDraw();return;}
  st.drawing=[]; $('mapbox').classList.add('draw'); $('drawtip').hidden=false; $('sitedrawbtn').textContent='그리기 끝내기';
  $('siteres').textContent='지도에서 대지 꼭짓점을 차례로 누르세요.'; show('pmap'); drawSite();
};
async function finishDraw(){
  const pts=st.drawing||[];
  if(pts.length<3){st.drawing=null; $('mapbox').classList.remove('draw'); $('drawtip').hidden=true; $('sitedrawbtn').textContent='지도에 그리기';
    $('siteres').textContent=pts.length?'꼭짓점이 3개 이상이어야 합니다.':''; drawSite(); return;}
  const r=await fetch('/site',{method:'POST',body:JSON.stringify({source:st.src.id, ring:pts})}); const j=await r.json();
  if(!r.ok){$('siteres').innerHTML=`<span style="color:var(--err)">${esc(j.error)}</span>`;return;}
  setSite({...j,kind:'draw',ring:pts});
}
window.addEventListener('keydown',e=>{if(!st.drawing) return;
  if(e.key==='Escape'){st.drawing=null; $('mapbox').classList.remove('draw'); $('drawtip').hidden=true; $('sitedrawbtn').textContent='지도에 그리기'; $('siteres').textContent=''; drawSite();}
  else if(e.key==='Backspace'&&!/INPUT|TEXTAREA/.test(document.activeElement.tagName)){e.preventDefault(); st.drawing.pop(); drawSite();}
  else if(e.key==='Enter') finishDraw();});
function drawSite(){const ov=$('siteov'); if(!ov) return; ov.innerHTML=''; if(!st.src) return;
  const s=st.src.sheet, im=$('map'), W=s[2]-s[0], H=s[3]-s[1], sx=im.clientWidth/W, sy=im.clientHeight/H;
  const P=p=>`${(p[0]-s[0])*sx},${(s[3]-p[1])*sy}`;
  let h='';
  const sw=2/(window.ZMK||1);
  if(st.site&&st.site.rings) h+=st.site.rings.map(r=>`<polygon points="${r.map(P).join(' ')}" fill="rgba(29,78,216,.30)" stroke="#111" stroke-width="${sw}"/>`).join('');
  if(st.drawing&&st.drawing.length){
    h+=`<polyline points="${st.drawing.map(P).join(' ')}" fill="rgba(29,78,216,.18)" stroke="#111" stroke-width="${sw}" stroke-dasharray="5 3"/>`;
    h+=st.drawing.map((p,i)=>{const [x,y]=P(p).split(',');return `<circle cx="${x}" cy="${y}" r="${i?3.5:5.5}" fill="${i?'#111':'#1d4ed8'}" stroke="#fff" stroke-width="1.5"/>`}).join('');}
  if(st.secLine&&st.secLine.length){   // 단면선 A–A'
    const q=st.secLine.map(P); h+=`<polyline points="${q.join(' ')}" fill="none" stroke="#2563eb" stroke-width="${sw*1.5}"/>`;
    h+=q.map((pt,i)=>{const [x,y]=pt.split(',');return `<circle cx="${x}" cy="${y}" r="${4/(window.ZMK||1)}" fill="#2563eb" stroke="#fff" stroke-width="${1.5/(window.ZMK||1)}"/><text x="${+x+6/(window.ZMK||1)}" y="${+y-6/(window.ZMK||1)}" font-size="${12/(window.ZMK||1)}" fill="#2563eb" font-weight="700">${i?"A'":'A'}</text>`;}).join('');}
  ov.innerHTML=h;}

export {drawSite, finishDraw};
