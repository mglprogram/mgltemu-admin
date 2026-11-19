const L = require('C:/Users/saing/.agents/skills/lingxing-oms-order-edit-api/scripts/lib.js');
(async () => {
  const att = await L.attach(); await L.prepare(att.ws);
  const tries = [
    {platformCodes: 'temu'},
    {platformCode: 'temu'},
    {},
  ];
  for (const body of tries) {
    const out = await L.run(att.ws, `(async () => {
      const r = await window.__lxCall('POST','/woms/platform/order/manualRefresh', ${JSON.stringify(body)});
      return JSON.stringify({code:r.code, msg:r.msg||'', data:String(JSON.stringify(r.data||'')).slice(0,120)});
    })()`);
    console.log(JSON.stringify(body), '->', String(out).slice(0, 200));
    if (/"code":200/.test(out)) break;
  }
})();
