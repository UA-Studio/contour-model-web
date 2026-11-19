// 화면 흐름 점검: 잘못된 zip 오류, 도엽 밖 경고, 진행 표시, 완료, 버전 표기
// 사용: node ui_flow.js 포트 파일폴더 그림폴더   (playwright 는 한글 경로 업로드가 안 돼 영문 사본 사용)
const { chromium } = require('playwright');
const [port, dir, out] = [process.argv[2] || '8790', process.argv[3] || '/tmp/claude-0/ui', process.argv[4] || '.'];
const url = `http://127.0.0.1:${port}/`;
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  const txt = async () => (await p.evaluate(() => document.body.innerText)).replace(/\s+/g, ' ');
  await p.goto(url); console.log('버전 표기:', await p.textContent('.ver'));
  for (const f of ['bad.zip', 'noshp.zip']) {
    await p.setInputFiles('#file', `${dir}/${f}`); await p.waitForTimeout(4000);
    const t = await txt(); const m = t.match(/.{0,40}(zip|레이어|열 수 없)[^.]{0,120}/);
    console.log(`[${f}] 오류창 보임:`, !(await p.$eval('#err', e => e.hidden)), '| 문구:', m ? m[0] : '(없음)');
    await p.screenshot({ path: `${out}/ui_err_${f}.png` });
    await p.goto(url);
  }
  // 도엽 하나 + 대지 → 도엽 밖 안내
  await p.setInputFiles('#file', `${dir}/a.zip`); await p.waitForFunction(() => !document.getElementById('go').disabled, null, { timeout: 60000 });
  await p.evaluate(() => document.querySelectorAll('details').forEach(d => d.open = true));
  await p.setInputFiles('#sitefile', `${dir}/site.dxf`);
  await p.waitForFunction(() => /㎡|못|없/.test(document.getElementById('siteres').textContent), null, { timeout: 60000 });
  console.log('[도엽 1장 + 대지]', (await p.textContent('#siteres')).replace(/\s+/g, ' '));
  await p.evaluate(() => document.getElementById('siteres').scrollIntoView()); await p.screenshot({ path: `${out}/ui_out_sheet.png` });
  // 두 도엽 + 대지 → 실행
  await p.goto(url);
  await p.setInputFiles('#file', [`${dir}/a.zip`, `${dir}/b.zip`]); await p.waitForFunction(() => !document.getElementById('go').disabled, null, { timeout: 90000 });
  await p.evaluate(() => document.querySelectorAll('details').forEach(d => d.open = true));
  await p.setInputFiles('#sitefile', `${dir}/site.dxf`);
  await p.waitForFunction(() => /㎡|못|없/.test(document.getElementById('siteres').textContent), null, { timeout: 60000 });
  console.log('[도엽 2장 + 대지]', (await p.textContent('#siteres')).replace(/\s+/g, ' '));
  const t0 = Date.now(); await p.click('#go');
  for (const s of [8, 30]) { await p.waitForTimeout(s * 1000 - (Date.now() - t0) > 0 ? s * 1000 - (Date.now() - t0) : 0); await p.screenshot({ path: `${out}/ui_run_${s}s.png` }); }
  await p.waitForFunction(() => /끝났|완료|문제가 생겼/.test(document.body.innerText) && (!document.getElementById('err').hidden || document.querySelector('#dfiles li')), null, { timeout: 600000 });
  console.log('완료까지', Math.round((Date.now() - t0) / 1000), '초, 오류창:', !(await p.$eval('#err', e => e.hidden)));
  await p.waitForTimeout(8000); await p.screenshot({ path: `${out}/ui_done.png` });
  console.log('결과 파일 목록:', (await p.textContent('#dfiles')).replace(/\s+/g, ' ').slice(0, 300));
  console.log('페이지 오류', JSON.stringify(errs)); await b.close();
})().catch(e => { console.log('시험 실패', e.message); process.exit(1); });
