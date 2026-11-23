// 공개 서버 모드: 서버 설정 받기, 서버에서 막는 칸 숨기기, 의견 보내기
var SRV=false, SRVCFG=null;
(async()=>{try{SRVCFG=await (await fetch('/config')).json();}catch(e){return;}
  if(!SRVCFG.server) return;
  SRV=true; document.body.classList.add('srv');
  const fd=document.getElementById('aerialbox'); if(fd&&fd.closest('details')) fd.closest('details').id='filesdet';
  const L=SRVCFG.limits||{};
  ['w','h'].forEach(k=>{const e=document.getElementById(k); if(e) e.max=L.max_side||1000;});
  const wbox=document.getElementById('w'); if(wbox){const p=document.createElement('p'); p.className='srvnote';
    p.textContent=SRVCFG.dev?'개발자 모드: 하루 횟수 제한 없음 · 한 변 '+(L.max_side||1000)+'m까지':'이 사이트에서는 한 변 '+(L.max_side||1000)+'m까지, 하루 '+(L.runs_per_day||5)+'번까지 만들 수 있어요.';
    const row=wbox.closest('.row'); if(row) row.after(p);}
  const of=document.getElementById('openf'); if(of) of.textContent='전부 한 번에 받기 (zip)';
  const hd=document.querySelector('.sb-head .ver'); if(hd) hd.innerHTML+=' · <a href="/" style="color:inherit">소개</a>';
  document.getElementById('srvbar').hidden=false;
})();
(()=>{const g=id=>document.getElementById(id); let rate=null;
  g('fbstars').innerHTML=[1,2,3,4,5].map(n=>'<button type="button" data-n="'+n+'">'+n+'</button>').join('');
  g('fbstars').querySelectorAll('button').forEach(b=>b.onclick=()=>{rate=+b.dataset.n;
    g('fbstars').querySelectorAll('button').forEach(x=>x.classList.toggle('on',+x.dataset.n<=rate));});
  const open=()=>{g('fb').hidden=false; g('scrim').hidden=false; g('fbtext').focus();};
  const close=()=>{g('fb').hidden=true; g('scrim').hidden=true;};
  g('fbopen').onclick=open; g('fbclose').onclick=close;
  g('scrim').addEventListener('click',()=>{if(!g('fb').hidden) close();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!g('fb').hidden) close();});
  g('fbsend').onclick=async()=>{const text=g('fbtext').value.trim(); if(!text){g('fbres').textContent='내용을 적어 주세요.';return;}
    g('fbsend').disabled=true;
    try{const r=await fetch('/feedback',{method:'POST',body:JSON.stringify({text,kind:g('fbkind').value,rating:rate,contact:g('fbcontact').value.trim(),page:'app'})});
      const j=await r.json(); if(!r.ok) throw new Error(j.error);
      g('fbres').textContent='고마워요. 잘 받았습니다.'; g('fbtext').value='';}
    catch(e){g('fbres').textContent='보내지 못했어요: '+e.message;}
    g('fbsend').disabled=false;};
})();
