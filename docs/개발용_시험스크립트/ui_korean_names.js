// 한글 파일 이름(도엽 zip·대지 DXF)으로 올려도 대지 파일 읽기가 되는지 (v1.15.4: 요청 머리글에 한글이 들어가 fetch 실패)
// 사용: node ui_korean_names.js  (서버 8790, /tmp/claude-0/ui 의 w1.zip·w2.zip·wsite.dxf)
const { chromium } = require('playwright'); const fs=require('fs');
const f=(n,src)=>({name:n,mimeType:'application/zip',buffer:fs.readFileSync('/tmp/claude-0/ui/'+src)});
(async()=>{const b=await chromium.launch();const p=await b.newPage();p.on('pageerror',e=>console.log('err',String(e)));
p.on('request',r=>r.method()==='POST'&&console.log('req',r.url()));
await p.goto('http://127.0.0.1:8790/');
await p.setInputFiles('#file',[f('B010수치지도_37705027.zip','w1.zip'),f('B010수치지도_37705028.zip','w2.zip')]);
await p.waitForFunction(()=>!document.getElementById('go').disabled,null,{timeout:90000});
await p.evaluate(()=>document.querySelectorAll('details').forEach(d=>d.open=true));
await p.setInputFiles('#sitefile',{name:'wangsuk.dxf',mimeType:'application/dxf',buffer:fs.readFileSync('/tmp/claude-0/ui/wsite.dxf')});
await p.waitForFunction(()=>!/읽는 중/.test(document.getElementById('siteres').textContent),null,{timeout:200000});
console.log(await p.evaluate(()=>document.getElementById('siteres').innerText));await b.close();})();
