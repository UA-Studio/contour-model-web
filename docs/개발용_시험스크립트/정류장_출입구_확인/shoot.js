const { chromium } = require('playwright');
const views = JSON.parse(require('fs').readFileSync(process.argv[2]));
(async () => {
  const b = await chromium.launch({args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const p = await b.newPage({viewport:{width:1400,height:900}, deviceScaleFactor:1});
  p.on('console', m => console.log('console', m.text()));
  for (const v of views) {
    const qs = new URLSearchParams(v.q).toString();
    await p.setViewportSize({width:+(v.q.w||1400),height:+(v.q.h||900)}); await p.goto('http://localhost:8765/r.html?' + qs);
    await p.waitForFunction(() => document.title === 'done', null, {timeout: 120000});
    await p.screenshot({path: v.out});
    console.log('ok', v.out);
  }
  await b.close();
})();
