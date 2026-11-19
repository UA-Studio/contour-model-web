// 대지 파일 정리 화면 시험: 도면을 넣고 → '레이어 정리하기' → 역할 목록·미리보기 → (바꿀 역할) → 저장 → 표준 파일로 대지가 잡히는지. 화면 그림 저장.
// 사용: node ui_siteprep.js 포트 그림폴더   (한글 파일 이름 그대로 buffer 로 넣는다)
const { chromium } = require('playwright'); const fs = require('fs');
const [port, shots] = [process.argv[2] || '8790', process.argv[3] || '/tmp/claude-0/std/shots'];
fs.mkdirSync(shots, { recursive: true });
const F = (p, name) => ({ name: name || p.split('/').pop(), mimeType: 'application/octet-stream', buffer: fs.readFileSync(p) });
const cases = [
  { tag: '왕숙', zips: ['/home/claude/wangsuk/B010수치지도_37705027.zip', '/home/claude/wangsuk/B010수치지도_37705028.zip'], dxf: '/home/claude/wangsuk/왕숙 배치계획.dxf' },
  { tag: '역북동_배치안1', zips: ['/home/claude/rv/B010수치지도_37713007 (1).zip', '/home/claude/rv/B010수치지도_37713008.zip'], dxf: '/tmp/claude-0/ui/역북동 기숙사 배치안1.dxf' },
  { tag: '역북동_556-118', zips: ['/home/claude/rv/B010수치지도_37713007 (1).zip', '/home/claude/rv/B010수치지도_37713008.zip'], dxf: '/tmp/claude-0/ui/역북동 556-118번지 일원.dxf' },
];
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1500, height: 900 } });
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  for (const c of cases) {
    await p.goto(`http://127.0.0.1:${port}/`);
    await p.setInputFiles('#file', c.zips.map(z => F(z)));
    await p.waitForFunction(() => !document.getElementById('go').disabled, null, { timeout: 120000 });
    await p.evaluate(() => document.querySelectorAll('details').forEach(d => d.open = true));
    await p.setInputFiles('#sitefile', F(c.dxf));
    await p.waitForFunction(() => !/읽는 중/.test(document.getElementById('siteres').textContent), null, { timeout: 200000 });
    console.log(c.tag, '처음 읽기:', (await p.evaluate(() => document.getElementById('siteres').innerText)).replace(/\s+/g, ' '));
    await p.click('#siteres [data-prep]');
    await p.waitForSelector('#prep.on', { timeout: 120000 });
    const rows = await p.$$eval('.pp-row', rs => rs.filter(r => !/r7/.test(r.className)).map(r => r.dataset.l + '=' + r.querySelector('select').value));
    console.log('  쓰는 레이어:', rows.join(', '));
    await p.mouse.move(1400, 880); await p.waitForTimeout(500);
    await p.screenshot({ path: `${shots}/정리화면_${c.tag}.png` });
    await p.click('#ppsave');
    await p.waitForFunction(() => !document.getElementById('prep').classList.contains('on') || /주세요|못했/.test(document.getElementById('ppnote').textContent), null, { timeout: 200000 });
    console.log('  저장 뒤:', (await p.evaluate(() => document.getElementById('siteres').innerText)).replace(/\s+/g, ' '));
  }
  console.log('페이지 오류', JSON.stringify(errs)); await b.close();
})().catch(e => { console.log('시험 실패', e.message); process.exit(1); });
