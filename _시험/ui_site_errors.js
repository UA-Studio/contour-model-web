// 대지 파일 넣기가 '읽는 중'에 멈추지 않는지: 엉뚱한 도엽 + 왕숙 도면(오류 문구), 맞는 도엽 + 왕숙 도면(성공)
// 사용: node ui_site_errors.js 포트 파일폴더
const { chromium } = require('playwright');
const [port, dir] = [process.argv[2] || '8790', process.argv[3] || '/tmp/claude-0/ui'];
async function trySite(p, zips, dxf) {
  await p.goto(`http://127.0.0.1:${port}/`);
  await p.setInputFiles('#file', zips.map(z => `${dir}/${z}`));
  await p.waitForFunction(() => !document.getElementById('go').disabled, null, { timeout: 90000 });
  await p.evaluate(() => document.querySelectorAll('details').forEach(d => d.open = true));
  const t = Date.now();
  await p.setInputFiles('#sitefile', `${dir}/${dxf}`);
  await p.waitForFunction(() => !/읽는 중/.test(document.getElementById('siteres').textContent), null, { timeout: 200000 });
  return [(Date.now() - t) / 1000, (await p.evaluate(() => document.getElementById('siteres').innerText)).replace(/\s+/g, ' ')];
}
(async () => {
  const b = await chromium.launch(); const p = await b.newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  console.log('역북동 도엽 + 왕숙 도면:', await trySite(p, ['a.zip', 'b.zip'], 'wsite.dxf'));
  console.log('왕숙 도엽 + 왕숙 도면:', await trySite(p, ['w1.zip', 'w2.zip'], 'wsite.dxf'));
  console.log('왕숙 도엽 + 역북동 도면:', await trySite(p, ['w1.zip', 'w2.zip'], 'site.dxf'));
  console.log('페이지 오류', JSON.stringify(errs)); await b.close();
})().catch(e => { console.log('시험 실패', e.message); process.exit(1); });
