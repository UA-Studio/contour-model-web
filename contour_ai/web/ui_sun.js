// 3D 보기 해 그림자 (v2.2): 계절(춘분·하지·추분·동지)과 시각을 바꿔 그림자를 보고, 해 궤적을 켜고, 그림으로 저장.
// 해 위치는 모델 가운데 위도·경도(summary.json 의 center_latlon)와 한국 시간(UTC+9)으로 계산한다 (NOAA 근사식, 오차 1° 안팎).
// 설계 검토용이며 법정 일조 검토를 대신하지 않는다.
import * as THREE from 'three';
import {$} from './ui_common.js';

const DAYS=[['춘분',3,20],['하지',6,21],['추분',9,23],['동지',12,22]];
const rad=Math.PI/180;

// 해 고도·방위각(북쪽에서 시계 방향, 도). 시각은 한국 표준시(시, 소수)
export function sunPos(lat,lon,month,day,hourKST,year=2026){
  const doy=Math.round((Date.UTC(year,month-1,day)-Date.UTC(year,0,0))/864e5);
  const g=2*Math.PI/365*(doy-1+(hourKST-12)/24);
  const eqt=229.18*(0.000075+0.001868*Math.cos(g)-0.032077*Math.sin(g)-0.014615*Math.cos(2*g)-0.040849*Math.sin(2*g));
  const decl=0.006918-0.399912*Math.cos(g)+0.070257*Math.sin(g)-0.006758*Math.cos(2*g)+0.000907*Math.sin(2*g)-0.002697*Math.cos(3*g)+0.00148*Math.sin(3*g);
  const tst=hourKST*60+eqt+4*lon-60*9;               // 참태양시(분)
  const ha=(tst/4-180)*rad;
  const phi=lat*rad;
  const cz=Math.sin(phi)*Math.sin(decl)+Math.cos(phi)*Math.cos(decl)*Math.cos(ha);
  const zen=Math.acos(Math.min(1,Math.max(-1,cz)));
  let az=Math.atan2(Math.sin(ha),Math.cos(ha)*Math.sin(phi)-Math.tan(decl)*Math.cos(phi))/rad+180;
  return {alt:90-zen/rad, az:(az+360)%360};
}
// 모델 좌표: root 를 x축으로 -90° 돌렸으므로 동쪽 = +x, 북쪽 = -z, 위 = +y
function dirOf(p){const a=p.alt*rad, z=p.az*rad; return new THREE.Vector3(Math.cos(a)*Math.sin(z), Math.sin(a), -Math.cos(a)*Math.cos(z));}
function hhmm(h){const m=Math.round(h*60); return `${Math.floor(m/60)}:${String(m%60).padStart(2,'0')}`;}

let V=null, ll=null, on=false, day=1, hour=14, pathOn=true, arc=null, dot=null, home=null;
export function initSun(view){   // view = {R,S,C,sun,root,center,d,draw}
  V=view; ll=null; home=V.sun.position.clone();
  fetch('/result/summary.json?t='+Date.now()).then(r=>r.ok?r.json():null).then(s=>{
    ll=s&&s.center_latlon; $('sunbtn').hidden=!ll; if(!ll) close(); else if(on) apply();}).catch(()=>{$('sunbtn').hidden=true;});
  clearArc(); if(on) apply();
}
function clearArc(){ if(!V) return; [arc,dot].forEach(o=>{if(o){V.S.remove(o); o.geometry.dispose();}}); arc=dot=null; }
function drawArc(){
  clearArc(); if(!pathOn) return;
  const [, mo, dd]=DAYS[day], r=V.d*0.9, pts=[];
  for(let h=4;h<=20.01;h+=0.25){const p=sunPos(ll[0],ll[1],mo,dd,h); if(p.alt>-2) pts.push(dirOf(p).multiplyScalar(r).add(V.center));}
  if(pts.length>1){arc=new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),new THREE.LineBasicMaterial({color:0x2563eb})); V.S.add(arc);}
  const p=sunPos(ll[0],ll[1],mo,dd,hour);
  if(p.alt>0){dot=new THREE.Mesh(new THREE.SphereGeometry(V.d*0.018,16,12),new THREE.MeshBasicMaterial({color:0x2563eb}));
    dot.position.copy(dirOf(p).multiplyScalar(r).add(V.center)); V.S.add(dot);}
}
function apply(){
  if(!V||!ll) return;
  const [name, mo, dd]=DAYS[day], p=sunPos(ll[0],ll[1],mo,dd,hour);
  const up=p.alt>0.5;
  V.sun.position.copy(up?dirOf(p).multiplyScalar(V.d*2).add(V.center):home);
  V.sun.intensity=up?1.8+Math.min(1,p.alt/40)*0.6:0.15;
  V.sun.shadow.needsUpdate=true; drawArc(); V.draw();
  $('suninfo').textContent=up?`${name} ${hhmm(hour)} · 해 높이 ${p.alt.toFixed(1)}° · 방위 ${p.az.toFixed(0)}°`:`${name} ${hhmm(hour)} · 해가 졌어요`;
  document.querySelectorAll('#sundays button').forEach((b,i)=>b.classList.toggle('on',i===day));
}
function close(){on=false; $('sunpanel').hidden=true; $('sunbtn').classList.remove('on');
  if(V){V.sun.position.copy(home); V.sun.intensity=1.8; V.sun.shadow.needsUpdate=true; clearArc(); V.draw();}}

$('sunbtn').onclick=()=>{ if(on){close(); return;} on=true; $('sunpanel').hidden=false; $('sunbtn').classList.add('on'); apply(); };
document.querySelectorAll('#sundays button').forEach((b,i)=>b.onclick=()=>{day=i; apply();});
$('sunhour').oninput=e=>{hour=+e.target.value; apply();};
$('sunpath').onclick=()=>{pathOn=!pathOn; $('sunpath').classList.toggle('off',!pathOn); apply();};
$('sunsave').onclick=()=>{ if(!V) return; V.draw();
  V.R.domElement.toBlob(b=>{ if(!b) return; const a=document.createElement('a'); a.href=URL.createObjectURL(b);
    a.download=`그림자_${DAYS[day][0]}_${hhmm(hour).replace(':','시')}분.png`; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(()=>URL.revokeObjectURL(a.href),4000); },'image/png'); };
