(() => {
  const rows = Array.from(document.querySelectorAll('tr'))
    .filter(tr => tr.children.length === 7 && /SPU\s*ID/.test(tr.innerText || ''));

  const grab = (txt, re) => { const m = txt.match(re); return m ? m[1].trim() : ''; };

  const out = rows.map(tr => {
    const c = Array.from(tr.children).map(td => (td.innerText || '').trim());
    const info = c[4];
    const eff = c[6];
    const lines = eff.split('\n').map(s => s.trim()).filter(Boolean);

    let activity = '';
    const actLine = lines.find(l => /活动申报价/.test(l) && /\$/.test(l));
    if (actLine) {
      const m = actLine.match(/\$([\d.]+)/);
      if (m) activity = parseFloat(m[1]);
    }

    const monies = lines.filter(l => /^\$[\d.]+$/.test(l)).map(l => parseFloat(l.slice(1)));

    return {
      spu_id: grab(c[1], /SPU\s*ID[：:]\s*(\S+)/),
      title: (c[1].split('\n')[1] || '').trim(),
      site: grab(c[1], /站点[：:]\s*(\S+)/),
      sales7: c[2],
      flow_status: c[3],
      skc_id: grab(info, /SKC\s*ID[：:]\s*(\S+)/),
      sku_code: grab(info, /货号[：:]\s*(\S+)/),
      spec: (info.split('\n')[0] || '').trim(),
      status: c[5],
      current_price: monies.length ? monies[0] : '',
      target_price: monies.length > 1 ? monies[1] : '',
      activity_price: activity
    };
  });
  return JSON.stringify(out);
})()