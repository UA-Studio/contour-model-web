// 오류 안내(무엇이 · 왜 · 어떻게)와 처음 열 때 이어 보기
// 화면(index.html)이 ui_main.js 를 통해 불러옵니다. 다른 칸과 함께 쓰는 값은 ui_common.js 의 st 에 있습니다.
import {$, esc, st} from './ui_common.js';
import {show} from './ui_tabs.js';
import {collect, mode, poll, runView, start} from './ui_run.js';
import {openResult} from './ui_results.js';

// 오류 안내: 무엇이 · 왜 · 어떻게
function friendly(err){
  const e=String(err||''), first=e.split('\n')[0];
  const base={tech:e};
  if(/먼저 수치지도/.test(e)) return {...base,what:'수치지도가 아직 없어요.',why:'모델을 만들 지형·건물 자료가 필요해요.',how:'01번 칸에 수치지도 2.0 (SHP) zip을 넣어 주세요.',acts:[['zip 고르기',()=>{hideErr();$('file').click();},1]]};
  if(/이미 만들고/.test(e)) return {...base,what:'이미 만들고 있는 모델이 있어요.',why:'한 번에 하나씩만 만들 수 있어요.',how:'지금 것이 끝나거나 멈춘 뒤 다시 눌러 주세요.',acts:[['생성 과정 보기',()=>{hideErr();show('plive');},1]]};
  const fm=e.match(/(도로시설 위치|가로등 위치|항공사진) 파일을 찾지 못했습니다: (.*)/);
  if(fm) return {...base,what:`${fm[1]} 파일을 찾지 못했어요.`,why:`적어 둔 경로(${fm[2]})에 파일이 없어요.`,how:'자료 파일 칸의 경로를 고치거나 비워 두세요. 비우면 그 자료 없이 만들어요.',
    acts:[['자료 파일 칸 열기',()=>{hideErr();document.querySelectorAll('details.more')[1].open=true;},1]]};
  if(/분 안에 끝나지 않아/.test(e)) return {...base,what:'시간 제한 안에 끝나지 않았어요.',why:first,how:'범위를 줄이거나(예: 500m) 건물 디테일을 낮춰 다시 만들어 보세요.',
    acts:[['디테일 1로 다시 만들기',()=>{$('detail').value='1';$('detail').dispatchEvent(new Event('change'));start(collect());},1],['기록 보기',()=>{hideErr();show('plog');}]]};
  if(/MemoryError|메모리/.test(e)) return {...base,what:'컴퓨터 메모리가 모자랐어요.',why:'범위가 넓거나 건물 디테일이 높으면 메모리를 많이 써요.',how:'범위를 줄이거나 건물 디테일을 낮춰 다시 만들어 보세요.',
    acts:[['디테일 1로 다시 만들기',()=>{$('detail').value='1';$('detail').dispatchEvent(new Event('change'));start(collect());},1]]};
  if(/HTTP|40[13]|Timeout|timed out|Connection|SSL|인증키|key/i.test(e)) return {...base,what:'인터넷 자료를 받지 못했어요.',why:'인증키가 틀렸거나, 서비스 신청이 안 되어 있거나, 인터넷 연결이 끊겼어요.',how:'인증키 창에서 연결 시험을 눌러 어느 키가 문제인지 확인해 주세요.',
    acts:[['인증키 확인하기',()=>{hideErr();window.openKeys&&window.openKeys();},1]]};
  if(/건물 레이어|레이어가 없/.test(e)) return {...base,what:'수치지도에 필요한 레이어가 없어요.',why:first,how:'국토정보플랫폼에서 "수치지도 2.0 (SHP)" 형식으로 받은 zip인지 확인해 주세요.',acts:[['다른 zip 넣기',()=>{hideErr();$('s1chg').click();$('file').click();},1]]};
  return {...base,what:'모델을 만드는 중에 문제가 생겼어요.',why:first.replace(/^\w+Error: /,''),how:'설정을 조금 바꿔 다시 만들어 보세요. 계속되면 아래 기술 정보를 복사해 알려 주세요.',
    acts:[['다시 만들기',()=>start(st.lastBody||collect()),1],['기록 보기',()=>{hideErr();show('plog');}]]};
}
function showErr(o){
  $('ewhat').textContent=o.what; $('ewhy').textContent=o.why; $('ehow').textContent=o.how;
  $('etech').hidden=!o.tech; $('etechp').textContent=o.tech||'';
  const a=$('eacts'); a.innerHTML='';
  (o.acts||[]).concat([['닫기',hideErr]]).forEach(([t,fn,pri])=>{const b=document.createElement('button'); b.type='button'; b.textContent=t; if(pri) b.className='pri'; b.onclick=fn; a.appendChild(b);});
  $('err').hidden=false;
}
function hideErr(){$('err').hidden=true;}

(async()=>{try{const j=await (await fetch('/status')).json();
  if(j.running){mode('run'); runView(0,'준비 중',j.elapsed); show('plive'); st.timer=setInterval(poll,1000); return;}
  if(j.done&&!j.error&&!j.cancelled){await poll(); return;}
  const r=await (await fetch('/recent')).json(); st.recentItems=r.items||[];
  if(st.recentItems.length){$('mapempty').innerHTML='수치지도 zip과 대지 도면을 왼쪽 01번 칸에 넣으면 지도가 여기에 나와요.<br><br><span style="font-size:12.5px">지난 결과 열기: '+
    st.recentItems.slice(0,5).map(x=>`<button type="button" class="lk2" data-d="${esc(x.dir)}" style="margin:0 6px">${esc(x.dir)}</button>`).join('')+'</span>';
    $('mapempty').querySelectorAll('[data-d]').forEach(b=>b.onclick=()=>openResult(b.dataset.d));}
}catch(e){}})();

export {friendly, hideErr, showErr};
