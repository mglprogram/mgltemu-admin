(() => {
  const out = {
    sectionRadios: Array.from(
      document.querySelectorAll('.flux-switch .el-radio-group')[1]
        ?.querySelectorAll('.el-radio-button') || []
    ).map(b => (b.innerText || '').replace(/\s+/g, ' ').trim()),
    stats: Array.from(document.querySelectorAll('.flux-stat__label')).map((e, i) => ({
      label: e.innerText.trim(),
      value: (document.querySelectorAll('.flux-stat__value')[i] || {}).innerText,
    })),
    alert: (document.querySelector('.flux-alert') || {}).innerText || null,
    cols: Array.from(document.querySelectorAll('.flux-table-card thead th'))
      .map(t => t.innerText.trim()).filter(Boolean),
    firstRows: Array.from(document.querySelectorAll('.flux-table-card tbody tr'))
      .slice(0, 3).map(tr => Array.from(tr.querySelectorAll('td'))
        .map(td => (td.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 42))),
  };
  return JSON.stringify(out);
})()