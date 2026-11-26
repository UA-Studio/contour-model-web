// 3. 실행: 설정 모으기, 만들기 시작·멈춤, 진행 막대
// 화면(index.html)이 ui_main.js 를 통해 불러옵니다. 다른 칸과 함께 쓰는 값은 ui_common.js 의 st 에 있습니다.
import {$, st} from './ui_common.js';
import {friendly, hideErr, showErr} from './ui_errors.js';
import {show} from './ui_tabs.js';
import {clampBox, drawSq, rangeView, texts} from './ui_map.js';
import {doneView, planViews} from './ui_results.js';
import {load3d} from './ui_view3d.js';

// 3. 실행 (v1.5: 왼쪽 칸이 설정 → 만드는 중 → 완성 으로 바뀜, 오류는 무엇이·왜·어떻게)
const STEPS=['수치지도 읽기','등고선 · 계단판','건물 · 길 · 가로수 배치','경사면 · 건물 터','Rhino · 레이저 도면 쓰기','완료'];
let stepAt={};
function mode(m){
  $('setup').hidden=m!=='setup'; $('runp').hidden=m!=='run'; $('donep').hidden=m!=='done';
  $('footSetup').hidden=m!=='setup'; $('footRun').hidden=m!=='run'; $('footDone').hidden=m!=='done';
}
window.mode=mode;
function collect(){
  // 결과 폴더 이름: 대지 이름 → 가운데 지명 → 도엽 번호
  const label=(st.site&&st.site.label?String(st.site.label).replace(/\.[^.]+$/,''):'')||$('place').value||st.src.label||st.src.file.replace(/\.zip$/i,'');
  return {source:st.src.id, xy:st.center, w:+$('w').value, h:st.shape==='circle'?+$('w').value:+$('h').value, shape:st.shape||'square', outputs:[...document.querySelectorAll('#outchips button.on')].map(b=>b.dataset.k), scale:+$('scale').value, interval:+$('interval').value,
    terrain:$('terrain').value, terrain_fit:(document.querySelector('#fitseg button.on')||{}).dataset?.v||'smooth', green:$('green').checked, pads:$('pads').checked, street_trees:$('street').checked, floor_height:+$('floor').value,
    tree_hole:+$('hole').value, vworld_key:$('vkey').value||$('vkey2').value, data_key:$('dkey').value, vworld_domain:$('vdomain').value, aerial_file:$('afile').value, lights:$('lights').checked, detail:+$('detail').value, facade_radius:$('fradius').value?+$('fradius').value:null, structures:$('structs').checked, light_file:$('lfile').value, road_detail:$('roadx').checked, road_files:$('rfile').value, hd_dir:$('hddir').value.trim(), dem_files:$('demfile').value.trim()||null, seoul_key:$('skey').value, safemap_key:$('smkey').value, seoul_service:$('ssvc').value, safemap_layer:$('smlayer').value, safemap_light_layer:$('smlight').value, seoul_code:$('scode').value, site_address:(st.site&&st.site.kind==='addr'||$('lawbody').innerHTML)?($('saddr').value.trim()||null):null, site_file:st.site&&st.site.kind==='file'?st.site.path:null, site_ring:st.site&&st.site.kind==='draw'?st.site.ring:null, site_clear:!!st.site&&$('siteclear').checked, site_keep_terrain:!!st.site&&$('sitekeep').checked, road_thickness:+$('roadt').value, sketchup:$('skp').checked, year:$('year').value||null, out:$('out').value.trim()||null, label};
}
async function start(body){
  hideErr();
  // 범위가 도엽보다 크면 돌리기 전에 알려 준다
  const W=st.src.sheet[2]-st.src.sheet[0], H=st.src.sheet[3]-st.src.sheet[1];
  if(body.w>W+1||body.h>H+1){
    const nw=Math.floor(Math.min(body.w,W)/10)*10, nh=Math.floor(Math.min(body.h,H)/10)*10;
    showErr({what:`고른 범위(${body.w} × ${body.h} m)가 넣은 도엽보다 커요.`,
      why:`넣은 도엽 전체가 ${Math.round(W)} × ${Math.round(H)} m라서, 나머지에는 지형 자료가 없어요.`,
      how:'범위를 줄이거나, 옆 도엽 zip을 함께 넣어 주세요.',
      acts:[[`${nw} × ${nh} m로 줄여서 만들기`,()=>{$('w').value=nw;$('h').value=nh;drawSq();texts();start(collect());},1],
            ['옆 도엽 넣기',()=>{hideErr();$('file').click();}]]});
    return;
  }
  if(st.site&&st.site.bounds&&st.src&&body.xy){const sb=st.site.bounds, bx=clampBox();
    const ix=Math.max(0,Math.min(sb[2],bx[2])-Math.max(sb[0],bx[0])), iy=Math.max(0,Math.min(sb[3],bx[3])-Math.max(sb[1],bx[1]));
    const frac=ix*iy/Math.max(1,(sb[2]-sb[0])*(sb[3]-sb[1]));
    if(frac<0.5&&!body._ok){showErr({what:`대지가 모델 범위에 ${frac<0.01?'들어 있지 않아요':'절반도 안 들어가요'}.`,why:'지도를 눌러 범위 가운데를 다른 곳으로 옮긴 것 같아요.',how:'대지에 맞춰 만들거나, 지금 범위 그대로 만들 수 있어요.',
      acts:[['대지에 맞춰 만들기',()=>{hideErr(); st.center=st.site.xy; st.centerMoved=false; drawSq(); rangeView(); start(collect());},1],['지금 범위로 만들기',()=>{hideErr(); start({...body,_ok:1});}]]}); return;}}
  const r=await fetch('/run',{method:'POST',body:JSON.stringify(body)}); const j=await r.json();
  if(!r.ok){showErr(friendly(j.error));return;}
  st.lastBody=body; stepAt={}; sub=null; st.resultReady=false; $('plog').textContent=''; st.nLog=0; st.nEv=0; st.live.reset();
  $('rsum').textContent=`${body.w} × ${body.h} m · 1:${body.scale} · ${{combo:'계단 + 경사',step:'계단판만',slope:'경사면만'}[body.terrain]} · 디테일 ${body.detail}`;
  runView(0,'준비 중'); mode('run'); show('plive');
  st.timer=setInterval(poll,1000);
}
$('go').onclick=()=>start(collect());
$('stop').onclick=async()=>{$('stop').disabled=true; $('stop').textContent='멈추는 중…'; await fetch('/cancel',{method:'POST',body:'{}'});};
$('again').onclick=()=>{if(!st.lastBody&&!st.src){mode('setup');show('pmap');return;} start(st.lastBody||collect());};
$('change').onclick=()=>{mode('setup'); show('pmap');};
$('openf').onclick=()=>SRV?(location.href='/download'):fetch('/open',{method:'POST',body:'{}'});
function mmss(t){t=Math.max(0,Math.round(+t||0));const h=Math.floor(t/3600),m=Math.floor(t%3600/60),x=t%60;return h?`${h}시간 ${m}분 ${x}초`:m?`${m}분 ${x}초`:`${x}초`;}
function mss(t){t=Math.max(0,Math.round(+t||0));return `${Math.floor(t/60)}:${String(t%60).padStart(2,'0')}`;}
function setStatus(kind,html,pct){const s=$('status'); s.className='status '+kind;
  s.innerHTML=html+(pct!=null?`<div class="bar"><i style="width:${pct}%"></i></div>`:'');}
// 저장 단계(5) 세부: 파일 종류별 몫 [시작, 끝]. 저장(디스크 쓰기)처럼 오래 걸리는 칸은 예상 시간으로 막대를 채운다
const SUBW={Rhino:[0,.7],STL:[.7,.9],SketchUp:[.9,.95],'레이저':[.95,1]};
let sub=null;
function subFrac(){if(!sub) return 0; const w=SUBW[sub.file]||[0,1]; let f=0;
  if(sub.est){f=Math.min(.97,(Date.now()-sub.seen)/1000/Math.max(sub.est,1)); if(sub.file==='Rhino') f=.2+.8*f;}
  else if(sub.n){f=(sub.r+1)/sub.n; if(sub.file==='Rhino') f*=.2;}
  else if(sub.file==='Rhino') f=.1;
  return w[0]+(w[1]-w[0])*f;}
function subText(){if(!sub) return ''; const nm={Rhino:'Rhino 파일',STL:'3D 프린트 STL',SketchUp:'스케치업',레이저:'레이저 판'}[sub.file]||sub.file;
  let t=`${nm} · ${sub.x}${sub.n?` ${(sub.r+1).toLocaleString()} / ${sub.n.toLocaleString()}`:''}`;
  if(sub.est){const left=Math.round(sub.est-(Date.now()-sub.seen)/1000); t+=left>2?` · 약 ${left}초 남음`:' · 거의 다 됐어요';}
  return t;}
// 위성영상·건축물대장 조회처럼 몇 분 걸리는 칸이 있어, 멈춘 게 아니라는 걸 보이게 마지막 기록 한 줄을 보여 준다
function lastLine(){const ls=($('plog')?.textContent||'').trim().split('\n'); const l=(ls.pop()||'').trim(); return /^\d\/6 /.test(l)?'':l.slice(0,60);}
function runView(k,label,el){
  $('rbig').textContent=k?`${Math.min(k,6)} / 6 단계`:'준비 중';
  $('rnow').textContent=`${label} · ${mmss(el||0)} 지남`;
  const inSub=k===5&&sub; $('rsub2').textContent=inSub?subText():lastLine();
  $('rbar').style.width=Math.max(3,(k-1+(inSub?subFrac():.5))/6*100)+'%';
  $('rsteps').innerHTML=STEPS.map((n,i)=>{const s=i+1, st=s<k?'done':s===k?'now':'todo';
    const t=st==='done'&&stepAt[s+1]!=null?mss(stepAt[s+1]-stepAt[s]):st==='now'&&stepAt[s]!=null?mss((el||0)-stepAt[s]):'';
    return `<li class="${st}"><span class="d">${st==='done'?'✓':''}</span><span>${n}</span><span class="t">${t}</span></li>`;}).join('');
}
// 프로그램이 Rhino 파일을 디스크에 쓰는 동안은 물음에 답하지 못하므로, 예상 시간이 있으면 화면이 스스로 센다
let lastRun=null;
setInterval(()=>{if(!lastRun||$('runp').hidden||!sub||!sub.est) return; runView(lastRun[0],lastRun[1],lastRun[2]+(Date.now()-lastRun[3])/1000);},500);
async function poll(){
  const j=await (await fetch('/status?since='+st.nLog+'&ev='+st.nEv)).json();
  if(j.running===false&&!j.done){   // 서버가 다시 켜져(새 판 배포 등) 만들던 작업이 사라짐: 0초에서 멈춘 채 기다리지 않게
    lastRun=null; clearInterval(st.timer); $('stop').disabled=false; $('stop').textContent='멈추기'; st.live.finish(false); mode('setup');
    showErr({what:'만들던 작업이 끊겼어요.',why:'만드는 도중에 서버가 다시 켜졌어요 (새 판 배포 등). 그동안 만든 것은 남지 않아요.',
      how:st.lastBody?'설정은 그대로 두고 다시 만들기를 눌러 주세요.':'만들기를 한 번 더 눌러 주세요. 수치지도가 안 보이면 01번 칸에 다시 넣어 주세요.',
      acts:st.lastBody?[['다시 만들기',()=>{hideErr();start(st.lastBody);},1]]:[]});
    return;}
  if(j.events){st.live.push(j.events); st.nEv=j.ne;
    for(const ev of j.events) if(ev.t==='sub') sub={...ev,seen:Date.now()}; }
  if(j.lines&&j.lines.length){$('plog').textContent+=j.lines.join('\n')+'\n'; st.nLog=j.n; $('plog').scrollTop=1e9;}
  const all=[...$('plog').textContent.matchAll(/(\d)\/6 ([^\n]*)/g)];
  all.forEach(m=>{if(stepAt[+m[1]]==null) stepAt[+m[1]]=j.elapsed;});
  const step=all.pop(), k=step?+step[1]:0;
  if(!j.done&&j.queue){runView(0,`순서 기다리는 중 · 앞에 ${j.queue}명`,0);return;}
  if(!j.done){lastRun=[k,step?step[2]:'준비 중',j.elapsed,Date.now()]; runView(k,lastRun[1],j.elapsed);return;}
  lastRun=null;
  clearInterval(st.timer); $('stop').disabled=false; $('stop').textContent='멈추기'; st.live.finish(!j.error&&!j.cancelled, j.cancelled?'멈추기를 눌러 멈췄어요.':(j.error?friendly(j.error).what:''));
  if(j.cancelled){mode('setup'); setStatus('run','멈췄어요. 설정을 바꿔 다시 만들 수 있어요.'); return;}
  if(j.error){mode('setup'); const f=friendly(j.error); if(j.refunded) f.how+=' 이번 실패는 오늘 만들기 횟수에서 빼지 않았어요.'; showErr(f); return;}
  doneView(j);
  planViews((j.summary||{}).analysis);
  load3d();
}

export {collect, mmss, mode, poll, runView, start};
