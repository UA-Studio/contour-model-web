// 1. 파일 넣기: 도엽 zip·대지 도면 올리기, 인증키·자료 파일 칸 기억
// 화면(index.html)이 ui_main.js 를 통해 불러옵니다. 다른 칸과 함께 쓰는 값은 ui_common.js 의 st 에 있습니다.
import {$, esc, st} from './ui_common.js';
import {setSite, siteUpload} from './ui_site.js';
import {drawSq, rangeView, resetZoom} from './ui_map.js';
import {show} from './ui_tabs.js';
import {drawSite} from './ui_prep.js';

// 1. 파일 넣기 (v2.0): 도엽 zip 과 대지 도면(DXF 등)을 한 칸에 함께. 순서 상관없음, 옆 도엽은 더해서 이어 붙임
const drop=$('drop');
drop.onclick=()=>$('file').click();
$('file').onchange=e=>{const fs=[...e.target.files]; e.target.value=''; fs.length&&addFiles(fs);};
drop.ondragover=e=>{e.preventDefault();drop.classList.add('on')};
drop.ondragleave=()=>drop.classList.remove('on');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('on');e.dataTransfer.files.length&&addFiles([...e.dataTransfer.files])};
let loading=[];
const isSheet=f=>/\.zip$/i.test(f.name), isSiteF=f=>/\.(dxf|geojson|json|shp)$/i.test(f.name);
async function addFiles(files){
  const zips=files.filter(isSheet), sites=files.filter(isSiteF), other=files.filter(f=>!isSheet(f)&&!isSiteF(f));
  $('srcinfo').innerHTML=other.length?`<span style="color:var(--err)">${esc(other.map(f=>f.name).join(', '))}: 수치지도 zip 이나 대지 도면(DXF·GeoJSON)이 아니라서 넣지 않았어요.</span>`:'';
  if(sites.length) st.pendingSite=sites[sites.length-1];
  if(zips.length){ await upload(zips, !!st.src); }
  renderList();
  if(st.pendingSite&&st.src){const f=st.pendingSite; st.pendingSite=null; await siteUpload(f);}
  else if(st.pendingSite&&!zips.length) renderList();
}
async function upload(files, add){
  const batch='b'+Date.now()+Math.random().toString(36).slice(2,8);
  let r,j; loading=files.map(f=>f.name); renderList();
  for(let i=0;i<files.length;i++){
    const f=files[i];
    $('srcinfo').textContent=files.length>1?`도엽 ${i+1}/${files.length} 올리는 중…${i+1===files.length?' · 이어 붙이는 중':''}`:`${f.name} 여는 중…`;
    const h={'X-Filename':encodeURIComponent(f.name),'X-Batch':batch,'X-Count':String(files.length)}; if(add&&st.src) h['X-Add']=encodeURIComponent(st.src.id);
    try{r=await fetch('/upload',{method:'POST',headers:h,body:f}); j=await r.json();}
    catch(err){loading=[]; $('srcinfo').innerHTML=`<span style="color:var(--err)">${esc(f.name)}: ${SRV?'서버에 닿지 못했어요. 서버가 켜지는 중일 수 있어요. 10초쯤 뒤 다시 넣어 주세요.':'프로그램에 닿지 못했어요. 검은 창이 켜져 있는지 확인해 주세요.'}</span>`; renderList(); return;}
    if(!r.ok){loading=[]; $('srcinfo').innerHTML=`<span style="color:var(--err)">${esc(f.name)}: ${esc(j.error)}</span>`; renderList(); return;}
  }
  loading=[]; const keep=st.site; st.src=j; st.names={}; st.lawRing=null;
  $('names').innerHTML=j.names.map(n=>{st.names[n.name]=n.xy;return `<option value="${esc(n.name)}">`}).join('');
  $('srcinfo').textContent=`${j.sheets>1?`도엽 ${j.sheets}장 이어 붙임 · 전체 `:'도엽 '}${j.size[0]}×${j.size[1]}m · 이름 있는 곳 ${j.names.length}개`;
  $('place').disabled=false; $('go').disabled=false; window.markLoaded&&window.markLoaded();
  resetZoom(); $('map').src='/overview.png?id='+j.id; $('mapwrap').hidden=false; $('mapempty').hidden=true;
  st.center=[(j.sheet[0]+j.sheet[2])/2,(j.sheet[1]+j.sheet[3])/2];
  $('map').onload=()=>{drawSq();drawSite();}; show('pmap');
  // 옆 도엽을 더했으면 이미 넣은 대지를 새 도엽 기준으로 다시 맞춘다
  if(keep&&(keep.kind==='file'||keep.kind==='draw')){
    const r2=await fetch('/site',{method:'POST',body:JSON.stringify({source:st.src.id,path:keep.kind==='file'?keep.path:null,ring:keep.kind==='draw'?keep.ring:null})});
    const j2=await r2.json(); if(r2.ok) setSite({...keep,...j2}); else setSite(null,false);
  } else setSite(keep&&keep.kind==='addr'?keep:null,false);
  renderList();
}
function renderList(){
  const L=[];
  if(st.src) for(const f of (st.src.files||[{name:st.src.file}]))
    L.push(`<li><span class="tag">도엽</span><span class="nm"><b>${esc(f.name)}</b><small>${f.sheet_no?'도엽 '+f.sheet_no:'수치지도'}</small></span><span class="okc">✓</span></li>`);
  for(const n of loading) L.push(`<li><span class="tag">도엽</span><span class="nm"><b>${esc(n)}</b><small>읽는 중…</small></span><span class="spin"></span></li>`);
  if(st.pendingSite) L.push(`<li><span class="tag s">대지</span><span class="nm"><b>${esc(st.pendingSite.name)}</b><small>${st.src?'읽는 중…':'수치지도 zip 을 읽은 뒤 바로 맞춰요'}</small></span>${st.src?'<span class="spin"></span>':''}</li>`);
  if(st.site){const how={file:'경계 파일',draw:'지도에 그린 대지',addr:'주소 필지'}[st.site.kind];
    const lv=st.site.levels?` · 레벨 ${st.site.levels}개`:'';
    L.push(`<li><span class="tag s">대지</span><span class="nm"><b>${esc(st.site.label||how)}</b><small>${how} · ${Math.round(st.site.area).toLocaleString()}㎡${lv}</small></span>`+
      (st.site.dxf?`<button type="button" class="lk2" data-prep="${esc(st.site.path)}">${st.site.standard?'레이어 다시':'레이어 정리'}</button>`:'<span class="okc">✓</span>')+`</li>`);}
  $('flist').innerHTML=L.join('');
  $('s1chg').hidden=!st.src;
  // 대지 안내: 도엽 밖이면 옆 도엽 넣기 버튼
  let n='';
  if(st.site&&st.site.in_sheet===false){
    const need=st.site.need&&st.site.need.length?st.site.need.join(', '):'';
    n=`<div class="snote">대지${st.site.out_pct?'의 '+st.site.out_pct+'%':' 일부'}가 넣은 도엽 밖${st.site.out_dirs&&st.site.out_dirs.length?'('+st.site.out_dirs.join('·')+'쪽)':''}에 있어요. 지금은 도엽 안 부분까지만 만들어요.<br><button type="button" id="addnb">옆 도엽${need?' '+esc(need):''} 넣기</button></div>`;
  } else if(st.site&&st.src) n=`<div class="snote b">대지가 넣은 도엽 안에 모두 들어 있어요. 범위를 대지에 맞췄어요.</div>`;
  if(st.site&&st.site.fit) n+=`<div class="info">${esc(st.site.fit)}</div>`;
  $('sitenote').innerHTML=n;
  const nb=$('addnb'); if(nb) nb.onclick=()=>$('file').click();
}
$('s1chg').onclick=()=>{st.src=null; setSite(null,false); st.pendingSite=null; $('go').disabled=true; $('place').disabled=true; $('srcinfo').textContent='';
  $('mapwrap').hidden=true; $('mapempty').hidden=false; $('zoomui').hidden=true; $('s1').classList.remove('done'); renderList(); rangeView();};

// 인증키는 이 PC 브라우저에만 기억
['vkey','vdomain','afile','dkey','saddr','lfile','rfile','hddir','demfile','skey','smkey','ssvc','smlayer','smlight','scode'].forEach(k=>{try{const v=localStorage.getItem('contour_'+k); if(v) $(k).value=v;}catch(e){}
  $(k).addEventListener('change',()=>{try{localStorage.setItem('contour_'+k,$(k).value.trim());}catch(e){}});});
$('street').addEventListener('change',()=>{$('aerialbox').hidden=!$('street').checked;});
$('lights').addEventListener('change',()=>{$('lightbox').hidden=!$('lights').checked;});
$('roadx').addEventListener('change',()=>{$('roadxbox').hidden=!$('roadx').checked;});
// 브이월드 키는 두 칸이 같은 값
$('vkey2').value=$('vkey').value;
$('vkey').addEventListener('input',()=>{$('vkey2').value=$('vkey').value;});
$('vkey2').addEventListener('input',()=>{$('vkey').value=$('vkey2').value; $('vkey').dispatchEvent(new Event('change'));});
$('testapi').onclick=async()=>{
  if(!st.src){$('testres').textContent='먼저 수치지도 zip을 넣어 주세요.';return;}
  $('testres').textContent='시험 중… (10~30초)';
  const r=await fetch('/testapi',{method:'POST',body:JSON.stringify({source:st.src.id, xy:st.center, w:+$('w').value, h:+$('h').value,
    vworld_key:$('vkey').value.trim(), data_key:$('dkey').value.trim(), vworld_domain:$('vdomain').value.trim(),
    seoul_key:$('skey').value.trim(), safemap_key:$('smkey').value.trim(), seoul_service:$('ssvc').value.trim(), safemap_layer:$('smlayer').value.trim(), safemap_light_layer:$('smlight').value.trim(), seoul_code:$('scode').value.trim()})});
  const j=await r.json();
  if(!r.ok){$('testres').innerHTML=`<span style="color:var(--err)">${j.error}</span>`;return;}
  $('testres').innerHTML=j.results.map(x=>`<div style="margin:4px 0"><span class="kb${x.ok?' ok':''}">${x.ok?'연결됨':'실패'}</span> <b>${x.name}</b>: ${String(x.msg).replace(/[<>&]/g,'')}</div>`).join('');
};

export {addFiles, renderList};
