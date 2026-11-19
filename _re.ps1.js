const L = require('C:/Users/saing/.agents/skills/lingxing-oms-order-edit-api/scripts/lib.js');
(async () => {
  const att = await L.attach(); await L.prepare(att.ws);
  const out = await L.run(att.ws, `(async () => {
    const r = await window.__lxCall('POST','/woms/platform/order/batchReAcquireLabel',
      {orderNos:["SO106261112001902"]});
    return JSON.stringify(r);
  })()`, 180000);
  console.log('reacquire:', out.slice(0,600));
  const info = await L.run(att.ws, `(async () => {
    const r = await window.__lxCall('GET','/woms/platform/order/orderInfo?orderNo=SO106261112001902');
    const d = r.data||{};
    return JSON.stringify({status:d.status,statusName:d.statusName,carrier:d.logisticsCarrier,
      tracking:d.trackingNo,label:d.labelUrl||d.labelPrintUrl, err:d.errorMsg||d.failReason||'',
      wh:d.sendWhCode, ch:d.logisticsChannelName});
  })()`, 60000);
  console.log('info:', info);
})();
