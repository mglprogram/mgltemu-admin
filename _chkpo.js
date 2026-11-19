const L = require('C:/Users/saing/.agents/skills/lingxing-oms-order-edit-api/scripts/lib.js');
(async () => {
  const att = await L.attach(); await L.prepare(att.ws);
  const out = await L.run(att.ws, `(async () => {
    const res = {};
    // 1) 精确按平台单号
    let body = {cancelWay:"",countKind:"orderCount",current:1,deliveryOptionTypes:"",
      logisticsChannels:"",markShipmentStatus:"",orderLabelCodes:[],pickupStatusList:[],
      platformCodes:"",platformOrderNo:"PO-211-13790281083512442",platformWarehouses:"",
      printingStatus:"",receiptCountries:"",sendWarehouses:"",siteCodes:"",size:10,
      status:"",storeCodes:"",transitStatusList:[]};
    let r = await window.__lxCall('POST','/woms/platform/order/list', body);
    res.exact = {code:r.code, total:(r.data&&r.data.total)||0};
    // 2) 不加单号，看最近订单里有没有（按平台代码 temu）
    body.platformOrderNo = '';
    body.platformCodes = 'temu';
    body.size = 20;
    r = await window.__lxCall('POST','/woms/platform/order/list', body);
    const rec = (r.data&&(r.data.records||r.data.list))||[];
    res.temuTotal = (r.data&&r.data.total)||0;
    res.temuSample = rec.slice(0,5).map(x=>({po:x.platformOrderNo, st:x.statusName, store:x.storeName}));
    return JSON.stringify(res);
  })()`, 180000);
  console.log(out);
})();
