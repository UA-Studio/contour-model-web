const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => m.type() === 'error' && errs.push(m.text()));
  await p.goto('http://127.0.0.1:8765/');
  await p.setInputFiles('#file', '/home/claude/tests/fake_sheet.zip');
  await p.waitForFunction(() => !document.getElementById('go').disabled, null, { timeout: 60000 });
  await p.evaluate(() => { document.getElementById('lawdet').open = true; document.getElementById('w').value = 300; document.getElementById('h').value = 300; });
  await p.setInputFiles('#sitefile', '/home/claude/tests/site.dxf');
  await p.waitForFunction(() => document.getElementById('siteres').textContent.includes('㎡'));
  await p.evaluate(() => document.getElementById('lawdet').scrollIntoView());
  await p.screenshot({ path: 'ui_1_file.png' });
  // 그리기
  await p.click('#sitedrawbtn');
  const r = await p.locator('#map').boundingBox();
  const pts = [[.2, .2], [.32, .2], [.32, .3], [.26, .34], [.2, .3]];
  for (const [u, v] of pts) await p.mouse.click(r.x + r.width * u, r.y + r.height * v);
  await p.screenshot({ path: 'ui_2_drawing.png' });
  await p.mouse.click(r.x + r.width * .2, r.y + r.height * .2);
  await p.waitForFunction(() => document.getElementById('siteres').textContent.includes('직접 그림'));
  await p.screenshot({ path: 'ui_3_drawn.png' });
  console.log(await p.textContent('#siteres'), '|', await p.inputValue('#w'));
  // 파일 경계로 되돌려 비우기 켜고 실행
  await p.setInputFiles('#sitefile', '/home/claude/tests/site.dxf');
  await p.waitForFunction(() => document.getElementById('siteres').textContent.includes('경계 파일'));
  await p.evaluate(() => { document.getElementById('siteclear').click(); document.getElementById('green').click(); });
  console.log(await p.textContent('#sitesum'));
  await p.click('#go');
  await p.waitForTimeout(25000); await p.screenshot({ path: 'ui_4_live.png' });
  await p.waitForFunction(() => !document.getElementById('donep').hidden, null, { timeout: 300000 });
  await p.waitForTimeout(3000);
  await p.evaluate(() => document.querySelector('.tabs button[data-p=p3d]').click());
  await p.waitForTimeout(6000); await p.screenshot({ path: 'ui_5_3d.png' });
  await p.evaluate(() => document.querySelector('.tabs button[data-p=pplan]').click());
  await p.waitForTimeout(1500); await p.screenshot({ path: 'ui_6_plan.png' });
  console.log('errors', JSON.stringify(errs)); await b.close();
})();
