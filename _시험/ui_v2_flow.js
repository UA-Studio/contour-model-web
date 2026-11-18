const { chromium } = require('playwright'); const fs=require('fs');
const D='/tmp/claude-0/ui', O=D+'/s3', url='http://127.0.0.1:8790/';
const f=(n,src,t)=>({name:n,mimeType:t||'application/zip',buffer:fs.readFileSync(D+'/'+src)});
(async()=>{
 const b=await chromium.launch({args:['--use-gl=swiftshader','--enable-unsafe-swiftshader']}); const p=await b.newPage({viewport:{width:1440,height:900}});
 const errs=[]; p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>m.type()==='error'&&errs.push(m.text()));
 const shot=async n=>p.screenshot({path:`${O}/${n}.png`,timeout:90000});
 await p.goto(url); await p.waitForTimeout(1000); await shot('01_start');
 // 대지 먼저 + 도엽 하나
 await p.setInputFiles('#file',[f('역북동 556-118 대지.dxf','site.dxf','application/dxf'),f('(B090)수치지도_37713007_2025.zip','a.zip')]);
 await p.waitForFunction(()=>/㎡/.test(document.getElementById('flist').textContent),null,{timeout:120000}); await p.waitForTimeout(800);
 await shot('02_one_sheet'); console.log('list1:',(await p.textContent('#s1')).replace(/\s+/g,' '));
 // 옆 도엽 더하기
 await p.setInputFiles('#file',[f('(B090)수치지도_37713008_2025.zip','b.zip')]);
 await p.waitForFunction(()=>/도엽 2장/.test(document.getElementById('srcinfo').textContent)&&/㎡/.test(document.getElementById('flist').textContent),null,{timeout:120000}); await p.waitForTimeout(2500);
 await shot('03_two_sheets'); console.log('list2:',(await p.textContent('#s1')).replace(/\s+/g,' '),'|range:',(await p.textContent('#s2')).replace(/\s+/g,' '));
 // 지도 휠 확대
 const box=await (await p.$('#sq')).boundingBox(); await p.mouse.move(box.x+box.width/2,box.y+box.height/2);
 for(let i=0;i<6;i++){await p.mouse.wheel(0,-240); await p.waitForTimeout(80);} await p.waitForTimeout(3500); await shot('04_zoom');
 console.log('tile shown:', await p.$eval('#tile',t=>t.style.display));
 await p.mouse.move(700,450); await p.mouse.down(); await p.mouse.move(800,500,{steps:8}); await p.mouse.up(); console.log('after pan center ok:', await p.textContent('#rhow')); await p.waitForTimeout(2500); await shot('05_pan');
 // 세부 설정 펼친 모습
 await p.click('#optdet summary'); await p.waitForTimeout(300); await shot('06_opts'); await p.click('#optdet summary');
 // 레이어 정리 화면
 const pb=await p.$('#flist [data-prep]'); if(pb){await pb.click(); await p.waitForSelector('#prep.on',{timeout:60000}); await p.waitForTimeout(800); await shot('07_prep'); await p.click('#ppclose');}
 // 만들기
 const t1=Date.now(); await p.click('#go');
 const seen=new Set();
 while(true){ await p.waitForTimeout(1500); const t=await p.textContent('#rsub2').catch(()=>'');
   if(t&&!seen.has(t.split('·')[1])){seen.add(t.split('·')[1]); console.log(((Date.now()-t1)/1000).toFixed(0)+'s', t, '| bar', await p.$eval('#rbar',e=>e.style.width));}
   if(/디스크에 저장/.test(t)&&!fs.existsSync(O+'/08_saving.png')) {await p.waitForTimeout(6000); await shot('08_saving'); console.log('saving text later:', await p.textContent('#rsub2'));}
   if(await p.$eval('#donep',e=>!e.hidden)) break; if(Date.now()-t1>600000) break; }
 await p.waitForTimeout(6000); await shot('09_done');
 await p.click('#rsub [data-i="0"]'); await p.waitForTimeout(1500); await shot('10_plan');
 await p.reload(); await p.waitForTimeout(8000); await shot('12_reload'); console.log('after reload done shown:', await p.$eval('#donep',e=>!e.hidden));
 console.log('errs',JSON.stringify(errs)); await b.close();
})().catch(e=>{console.log('fail',e.message);process.exit(1)});
