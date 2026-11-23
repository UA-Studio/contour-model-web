// 2. 범위: 지도 위 네모, 대지에 맞춤, 지도 확대·이동
// 화면(index.html)이 ui_main.js 를 통해 불러옵니다. 다른 칸과 함께 쓰는 값은 ui_common.js 의 st 에 있습니다.
import {$, st} from './ui_common.js';
import {drawSite, finishDraw} from './ui_prep.js';
import {resize3d} from './ui_view3d.js';

// 2. 범위
$('place').oninput=()=>{const xy=st.names[$('place').value]; if(xy){st.center=xy;drawSq();}};
['w','h','scale','interval'].forEach(k=>$(k).oninput=()=>{drawSq();texts();});
function clampBox(){const s=st.src.sheet,w=+$('w').value||500,h=+$('h').value||500;
  let x0=Math.min(Math.max(st.center[0]-w/2,s[0]),s[2]-w), y0=Math.min(Math.max(st.center[1]-h/2,s[1]),s[3]-h);
  return [x0,y0,x0+w,y0+h];}
function drawSq(){if(!st.src) return; const s=st.src.sheet, im=$('map'), b=clampBox(), W=s[2]-s[0], H=s[3]-s[1];
  const sx=im.clientWidth/W, sy=im.clientHeight/H, q=$('sq');
  Object.assign(q.style,{display:'block',left:(b[0]-s[0])*sx+'px',top:(s[3]-b[3])*sy+'px',width:(b[2]-b[0])*sx+'px',height:(b[3]-b[1])*sy+'px'});}
$('map').ondblclick=()=>{if(st.drawing&&st.drawing.length>=3) finishDraw();};
$('map').onclick=e=>{if(ZM.dragged){ZM.dragged=false;return;} const s=st.src.sheet, im=$('map'), r=im.getBoundingClientRect();
  if(st.drawing){
    const p=[s[0]+(e.clientX-r.left)/r.width*(s[2]-s[0]), s[3]-(e.clientY-r.top)/r.height*(s[3]-s[1])];
    if(e.detail>1) return;                         // 두 번 누르기의 둘째 누름은 점으로 넣지 않는다
    if(st.drawing.length>=3){const q=st.drawing[0], px=r.width/(s[2]-s[0]);
      if(Math.hypot((p[0]-q[0])*px,(p[1]-q[1])*px)<10){finishDraw();return;}}
    st.drawing.push([Math.round(p[0]*100)/100,Math.round(p[1]*100)/100]); drawSite(); return;}
  st.center=[s[0]+(e.clientX-r.left)/r.width*(s[2]-s[0]), s[3]-(e.clientY-r.top)/r.height*(s[3]-s[1])];
  $('place').value=''; st.centerMoved=true; drawSq(); rangeView();};
window.addEventListener('resize',()=>{resetZoom();drawSq();drawSite();resize3d();});
// 02 범위: 대지가 있으면 '대지에 맞춤' 한 줄로 접고, '바꾸기'를 눌렀을 때만 숫자 칸을 연다

function rangeView(){const w=+$('w').value, h=+$('h').value;
  $('rsz').textContent=`${w} × ${h} m`;
  $('rhow').textContent=st.site&&!st.centerMoved?'· 대지에 맞춤':st.site?'· 지도에서 고른 자리':st.src?'· 지도를 눌러 가운데를 고르세요':'';
  $('rangeedit').hidden=!!st.site&&!st.rangeEdit; $('s2chg').hidden=!st.site||st.rangeEdit;}
$('s2chg').onclick=()=>{st.rangeEdit=true; rangeView();};
function texts(){const sc=+$('scale').value||1000, w=+$('w').value, h=+$('h').value, iv=+$('interval').value;
  $('rangeinfo').textContent=`1:${sc} → ${+(w*1000/sc/10).toFixed(1)} × ${+(h*1000/sc/10).toFixed(1)} cm`; rangeView();
  $('sheetinfo').textContent=`1:${sc}에서 ${iv}m = 판 두께 ${(iv*1000/sc).toFixed(1)}mm`;}

// 지도 확대 (v2.0): 휠 = 마우스 자리로 확대·축소, 끌기 = 이동, 누르기 = 가운데 고르기. 확대하면 보이는 부분을 선명하게 다시 그린다
const ZM={k:1,x:0,y:0,drag:null,dragged:false,timer:null,req:0};
function zApply(){const b=$('mapbox'); b.style.transform=`translate(${ZM.x}px,${ZM.y}px) scale(${ZM.k})`;
  window.ZMK=ZM.k; $('sq').style.borderWidth=(2/ZM.k)+'px'; drawSite(); $('zhint').textContent=ZM.k>1.01?`${Math.round(ZM.k*100)}% · 끌어서 이동 · 누르면 가운데`:'휠로 확대 · 끌어서 이동 · 누르면 가운데';
  clearTimeout(ZM.timer); ZM.timer=setTimeout(zTile,280);}
function zClamp(){const im=$('map'), w=im.clientWidth, h=im.clientHeight;
  ZM.x=Math.min(0,Math.max(w-w*ZM.k,ZM.x)); ZM.y=Math.min(0,Math.max(h-h*ZM.k,ZM.y));}
function zoomAt(cx,cy,f){const r=$('mapbox').getBoundingClientRect(), bx=r.left-ZM.x, by=r.top-ZM.y;
  const lx=(cx-r.left)/ZM.k, ly=(cy-r.top)/ZM.k, k2=Math.min(16,Math.max(1,ZM.k*f));
  ZM.x=cx-bx-k2*lx; ZM.y=cy-by-k2*ly; ZM.k=k2; zClamp(); zApply();}
function resetZoom(){ZM.k=1;ZM.x=0;ZM.y=0; $('tile').style.display='none'; zApply();}
$('mapwrap').addEventListener('wheel',e=>{if(!st.src) return; e.preventDefault(); zoomAt(e.clientX,e.clientY,Math.exp(-e.deltaY*0.001));},{passive:false});
$('mapbox').addEventListener('mousedown',e=>{if(e.button!==0||!st.src) return; ZM.drag={x:e.clientX,y:e.clientY,ox:ZM.x,oy:ZM.y}; ZM.dragged=false;});
window.addEventListener('mousemove',e=>{const d=ZM.drag; if(!d) return; const dx=e.clientX-d.x, dy=e.clientY-d.y;
  if(!ZM.dragged&&Math.hypot(dx,dy)<5) return; if(ZM.k<=1.001) return;
  ZM.dragged=true; $('mapbox').classList.add('grab'); ZM.x=d.ox+dx; ZM.y=d.oy+dy; zClamp(); zApply();});
window.addEventListener('mouseup',()=>{ZM.drag=null; $('mapbox').classList.remove('grab'); setTimeout(()=>{ZM.dragged=false;},0);});
$('zin').onclick=()=>{const r=$('mapwrap').getBoundingClientRect(); zoomAt(r.left+r.width/2,r.top+r.height/2,1.6);};
$('zout').onclick=()=>{const r=$('mapwrap').getBoundingClientRect(); zoomAt(r.left+r.width/2,r.top+r.height/2,1/1.6);};
$('zfit').onclick=resetZoom;
async function zTile(){const t=$('tile'); if(!st.src||ZM.k<1.3){t.style.display='none';return;}
  const im=$('map'), w=im.clientWidth, h=im.clientHeight, vw=$('mapwrap').getBoundingClientRect(), r=$('mapbox').getBoundingClientRect();
  // 보이는 부분(지도 좌표 픽셀, 확대 전 기준)
  const l0=Math.max(0,(vw.left-r.left)/ZM.k), t0=Math.max(0,(vw.top-r.top)/ZM.k), l1=Math.min(w,(vw.right-r.left)/ZM.k), t1=Math.min(h,(vw.bottom-r.top)/ZM.k);
  if(l1<=l0||t1<=t0) return;
  const s=st.src.sheet, sx=(s[2]-s[0])/w, sy=(s[3]-s[1])/h, bb=[s[0]+l0*sx, s[3]-t1*sy, s[0]+l1*sx, s[3]-t0*sy];
  const px=Math.round((l1-l0)*ZM.k*Math.min(devicePixelRatio||1,2)), my=++ZM.req;
  const url=`/overview_tile.png?id=${encodeURIComponent(st.src.id)}&bbox=${bb.map(v=>v.toFixed(1)).join(',')}&px=${px}`;
  const img=new Image(); img.onload=()=>{if(my!==ZM.req) return; t.src=url; Object.assign(t.style,{display:'block',left:l0+'px',top:t0+'px',width:(l1-l0)+'px',height:(t1-t0)+'px'});}; img.src=url;}

texts();

export {clampBox, drawSq, rangeView, resetZoom, texts};
