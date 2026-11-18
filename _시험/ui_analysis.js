// 대지 분석 화면 확인: 도엽 2장 + 대지 → 만들기 → 완성 화면 숫자, 평면 탭의 경사·절토성토 전환
// 사용: node ui_analysis.js 포트 파일폴더 그림폴더
const { chromium } = require('playwright');
const [port, dir, out] = [process.argv[2] || '8790', process.argv[3] || '/tmp/claude-0/ui', process.argv[4] || '.'];
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto(`http://127.0.0.1:${port}/`);
  await p.setInputFiles('#file', [`${dir}/a.zip`, `${dir}/b.zip`]); await p.waitForFunction(() => !document.getElementById('go').disabled, null, { timeout: 90000 });
  await p.evaluate(() => document.querySelectorAll('details').forEach(d => d.open = true));
  await p.setInputFiles('#sitefile', `${dir}/site.dxf`);
  await p.waitForFunction(() => /㎡/.test(document.getElementById('siteres').textContent), null, { timeout: 60000 });
  await p.click('#go');
  await p.waitForFunction(() => !document.getElementById('donep').hidden || !document.getElementById('err').hidden, null, { timeout: 600000 });
  await p.waitForTimeout(3000); console.log('상태', await p.evaluate(() => [document.getElementById('donep').hidden, document.getElementById('err').hidden, location.href]));
  console.log('완성 숫자:', (await p.evaluate(() => document.getElementById('dfacts').innerText)).replace(/\s+/g, ' '));
  console.log('파일:', (await p.evaluate(() => document.getElementById('dfiles').innerText)).replace(/\s+/g, ' ').slice(0, 500));
  await p.screenshot({ path: `${out}/ui_done_facts.png`, timeout: 180000 });
  await p.click('button[data-p="pplan"]'); await p.waitForTimeout(1500);
  const btns = await p.$$('#planvw button'); console.log('평면 탭 버튼', btns.length);
  for (let i = 0; i < btns.length; i++) { await btns[i].click(); await p.waitForTimeout(1500); await p.screenshot({ path: `${out}/ui_plan_${i}.png`, timeout: 180000 }); }
  console.log('페이지 오류', JSON.stringify(errs)); await b.close();
})().catch(e => { console.log('시험 실패', e.message); process.exit(1); });
