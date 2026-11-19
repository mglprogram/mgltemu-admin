const L = require('C:/Users/saing/.agents/skills/lingxing-oms-order-edit-api/scripts/lib.js');
(async () => {
  const att = await L.attach(); await L.prepare(att.ws);
  const out = await L.run(att.ws, `(async () => {
    const names = {};
    for (let p = 1; p <= 3; p++) {
      const body = {cancelWay:"",countKind:"orderCount",current:p,deliveryOptionTypes:"",
        logisticsChannels:"",markShipmentStatus:"",orderLabelCodes:[],pickupStatusList:[],
        platformCodes:"",platformOrderNo:"",platformWarehouses:"",printingStatus:"",
        receiptCountries:"",sendWarehouses:"",siteCodes:"",size:100,status:"",
        storeCodes:"",transitStatusList:[]};
      const r = await window.__lxCall('POST','/woms/platform/order/list', body);
      const rec = (r.data&&(r.data.records||r.data.list))||[];
      rec.forEach(x => { names[(x.storeName||'?')+' / '+(x.storeCode||'?')] = (names[(x.storeName||'?')+' / '+(x.storeCode||'?')]||0)+1; });
    }
    return JSON.stringify(names);
  })()`, 240000);
  console.log(out.slice(0, 1200));
})();
