// 화면에서 왕숙: 도엽 2장(w1·w2.zip) + 도면 좌표 배치계획(wsite.dxf) → 대지 읽기 결과 → 만들기 → 완성
// 사용: node ui_wangsuk.js 포트 파일폴더 그림폴더   (playwright 는 한글 경로 업로드가 안 돼 영문 사본 사용)
const { chromium } = require('playwright');
const [port, dir, out] = [process.argv[2] || '8790', process.argv[3] || '/tmp/claude-0/ui', process.argv[4] || '.'];
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto(`http://127.0.0.1:${port}/`);
  await p.setInputFiles('#file', [`${dir}/w1.zip`, `${dir}/w2.zip`]);
  await p.waitForFunction(() => !document.getElementById('go').disabled, null, { timeout: 90000 });
  await p.evaluate(() => document.querySelectorAll('details').forEach(d => d.open = true));
  const t = Date.now();
  await p.setInputFiles('#sitefile', `${dir}/wsite.dxf`);
  await p.waitForFunction(() => /㎡|못|없|아니|맞추/.test(document.getElementById('siteres').textContent), null, { timeout: 60000 });
  console.log('대지 읽기', (Date.now() - t) / 1000, '초:', (await p.evaluate(() => document.getElementById('siteres').innerText)).replace(/\s+/g, ' '));
  await p.evaluate(() => document.getElementById('siteres').scrollIntoView());
  await p.waitForTimeout(1500);
  await p.screenshot({ path: `${out}/ui_w_site.png`, timeout: 120000 });
  await p.click('#go');
  await p.waitForFunction(() => !document.getElementById('donep').hidden || !document.getElementById('err').hidden, null, { timeout: 900000 });
  await p.waitForTimeout(2000);
  const st = await p.evaluate(() => [document.getElementById('donep').hidden, document.getElementById('err').hidden]);
  console.log('완성', !st[0], '오류창', !st[1]);
  if (!st[1]) console.log('오류:', await p.evaluate(() => document.getElementById('err').innerText));
  console.log('완성 숫자:', (await p.evaluate(() => document.getElementById('dfacts').innerText)).replace(/\s+/g, ' '));
  await p.click('button[data-p="pplan"]'); await p.waitForTimeout(2500);
  await p.screenshot({ path: `${out}/ui_w_plan.png`, timeout: 180000 });
  console.log('페이지 오류', JSON.stringify(errs)); await b.close();
})().catch(e => { console.log('시험 실패', e.message); process.exit(1); });
