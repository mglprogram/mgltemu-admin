#!/usr/bin/env node
/**
 * hx_ship_gw.js — 恒信/领星 OMS 真发货：走**内部网关**（紫鸟里已登录的 oms.xlwms.com 会话）。
 * （2026-11-12 实测跑通：BS 31 / BD 45 / BAN 10 单一次过审，全部到「待获取平台面单」）
 *
 * 为什么不用公开 OpenAPI（`hx_ship_api.py`）：
 *   · `outboundOrder/create` 只落**草稿（status=0）**，公开 API 里没有提交/审核接口；
 *   · 而且它要求的 `productList[].sku` 是系统SKU、必填 `countryRegionCode`，
 *     踩了两个大坑才跑通（见 hx_ship_api.py 注释）。
 *   UI 实际干的是两步，都有网关接口：
 *     `POST /woms/platform/order/update`        分配仓库&物流 + 产品配对（同一次提交）
 *     `POST /woms/platform/order/batchApprove`  审核 → 之后才会生成小包出库单
 *
 * 用法：
 *   node scripts/oms/hx_ship_gw.js inspect --po GSU1AU38U00N396
 *   node scripts/oms/hx_ship_gw.js ship --plan plan.json --carriers carriers.json [--apply]
 *
 * plan.json      : [{po, skus?:[{sku, qty, name}]}]   —— skus 可省，OMS 里已配对的会直接用
 * carriers.json  : {po: "CBS"}                          —— ★ 必带，见下面那段
 *
 * ★★ 承运商是死穴（2026-11-12 真机）：
 *   审核后如果 `logisticsCarrier` 是空，OMS 会回
 *   `生成小包出库单失败:null, please fill in the logistics carrier!`
 *   ⇒ 订单卡在「待获取平台面单（生成出库单失败）」，永远不会变「仓库处理中」。
 *   承运商要从**当批 SHEIN 面单**（`shein_label_<店>.json` 的 `carrier`）按单取，别写死：
 *     node -e "..." 或见 run_hx_fulfill 里生成 carriers.json 的那段
 *   已经审核过的单再调 update 会回 `code=400 订单不可编辑` —— 必须先在 OMS 里「转待处理」。
 */
'use strict';

const L = require('C:/Users/saing/.agents/skills/lingxing-oms-order-edit-api/scripts/lib.js');
const fs = require('fs');

const WH = 'USW01';
const CH_CODE = 'Upload_Shipping_Label';
const CH_NAME = '上传物流面单';

function log() {
  console.log.apply(console, arguments);
}

async function inspect(ws, po) {
  const rows = await L.findByPlatformOrderNo(ws, po);
  if (!rows.length) return { po, found: false };
  const row = rows[0];
  const info = await L.orderInfo(ws, row.orderNo);
  const addr = await L.addressInfo(ws, row.orderNo);
  return {
    po,
    found: true,
    row,
    orderNo: info.orderNo,
    status: info.status,
    statusName: info.statusName,
    sendWhCode: info.sendWhCode,
    channel: info.logisticsChannelCode,
    carrier: info.logisticsCarrier,
    country: addr.countryRegionCode,
    province: addr.provinceName,
    city: addr.cityName,
    zip: addr.postCode,
    products: (info.productInfoList || []).map((p) => ({
      platformSku: p.platformSku, sku: p.sku, qty: p.qty, platformQty: p.platformQty,
      name: p.productName,
    })),
  };
}

async function shipOne(ws, po, skus, apply, carriers) {
  const rows = await L.findByPlatformOrderNo(ws, po);
  if (!rows.length) return { po, ok: false, why: '平台订单查不到' };
  const row = rows[0];
  const info = await L.orderInfo(ws, row.orderNo);
  const addr = await L.addressInfo(ws, row.orderNo);
  // ★ OMS 侧可能**已经配对**（productInfoList[].sku 有值）——优先用它，
  //   只有在没配对时才用外部传进来的 skus。全空 ⇒ 交人工「产品配对」。
  const paired = (info.productInfoList || [])
    .filter((p) => p.sku)
    .map((p) => ({ sku: p.sku, qty: Number(p.qty || p.platformQty) || 1,
                   name: p.productName || '' }));
  const missing = (info.productInfoList || []).filter((p) => !p.sku)
    .map((p) => p.platformSku);
  let items = paired;
  if (!items.length) {
    if (!skus || !skus.length) {
      return { po, ok: false, why: '未做产品配对', missing };
    }
    items = skus;
  }
  if (missing.length) return { po, ok: false, why: '部分行未配对', missing };
  const names = await L.productNames(ws, items.map((s) => s.sku)).catch(() => ({}));
  items = items.map((s) => ({ sku: s.sku, qty: Number(s.qty) || 1,
                              name: s.name || names[s.sku] || '' }));
  const payload = L.buildPayload(info, addr, items);
  payload.sendWhCode = WH;
  payload.logisticsChannelCode = CH_CODE;
  payload.logisticsChannelName = CH_NAME;
  // ★ 2026-11-12 真机：不填承运商 → 审核后「生成小包出库单失败:null, please fill in
  //   the logistics carrier!」⇒ 订单卡在「待获取平台面单」，永远不会变仓库处理中。
  //   承运商来自当批 SHEIN 面单（每单实际买的那个），别写死。
  const carrier = (carriers && carriers[po]) || '';
  if (carrier) payload.logisticsCarrier = carrier;
  if (!apply) return { po, ok: true, dry: true, items, payload: payload };

  const up = await L.updateOrder(ws, payload);
  if (up.code !== 200) return { po, ok: false, why: 'update code=' + up.code + ' ' + up.msg };

  // 审核：先试 batchApprove，再回读状态
  const ap = await L.run(ws, `(async ()=>{
    const r = await window.__lxCall('POST','/woms/platform/order/batchApprove',
      {orderNos:[${JSON.stringify(row.orderNo)}]});
    return JSON.stringify(r);
  })()`);
  let apObj = {};
  try { apObj = JSON.parse(ap); } catch (e) { apObj = { raw: String(ap).slice(0, 200) }; }

  await L.sleep(1500);
  const after = await L.orderInfo(ws, row.orderNo);
  return { po, ok: true, orderNo: row.orderNo, approve: apObj,
           status: after.status, statusName: after.statusName,
           // eslint-disable-next-line no-underscore-dangle
  };
}

async function main() {
  const mode = process.argv[2] || 'inspect';
  const get = (k) => {
    const i = process.argv.indexOf('--' + k);
    return i >= 0 ? process.argv[i + 1] : '';
  };
  const att = await L.attach();
  if (!att) throw new Error('连不上 OMS 页面（紫鸟窗口里要开着 oms.xlwms.com 并已登录）');
  log('[attach] port=%s url=%s', att.port, att.url);
  await L.prepare(att.ws);

  if (mode === 'inspect') {
    const po = get('po');
    log(JSON.stringify(await inspect(att.ws, po), null, 1));
    return 0;
  }
  if (mode === 'ship') {
    const carriers = get('carriers')
      ? JSON.parse(fs.readFileSync(get('carriers'), 'utf-8').replace(/^\uFEFF/, ''))
      : {};
    const plan = JSON.parse(
      fs.readFileSync(get('plan'), 'utf-8').replace(/^\uFEFF/, ''));
    const apply = process.argv.includes('--apply');
    const out = [];
    for (const it of plan) {
      let r = null;
      for (let attempt = 1; attempt <= 3; attempt++) {
        try {
          r = await shipOne(att.ws, it.po, it.skus, apply, carriers);
          if (r && (r.ok || r.why === '未做产品配对' || r.why === '部分行未配对'
                    || r.why === '平台订单查不到')) break;
        } catch (e) {
          r = { po: it.po, ok: false, why: 'ERR ' + String(e.message || e).slice(0, 120) };
          await L.sleep(2500);
        }
      }
      if (it.store && r) r.store = it.store;
      log('%s %s', r && r.ok ? '✅' : '✗', JSON.stringify(r).slice(0, 300));
      out.push(r);
      await L.sleep(600);
    }
    const ok = out.filter((x) => x.ok && !x.dry).length;
    log('[done] %d/%d 提交成功（apply=%s）', ok, out.length, apply);
    fs.writeFileSync(get('out') || 'gw_ship_result.json',
      JSON.stringify(out, null, 1), 'utf-8');
    return 0;
  }
  throw new Error('未知模式 ' + mode);
}

main().then((c) => process.exit(c || 0)).catch((e) => {
  console.error('[FATAL]', e && e.message ? e.message : e);
  process.exit(1);
});
