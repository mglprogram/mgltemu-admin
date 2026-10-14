(() => {
  const g = document.querySelector('.flux-switch .el-radio-group');
  const groups = document.querySelectorAll('.flux-switch .el-radio-group');
  const out = {
    url: location.href,
    title: document.title,
    gateVisible: !!document.getElementById('gate'),
    groupCount: groups.length,
  };
  if (groups[0]) {
    out.storeRadios = Array.from(groups[0].querySelectorAll('.el-radio-button'))
      .map(b => (b.innerText || '').replace(/\s+/g, ' ').trim());
  }
  if (groups[1]) {
    out.sectionRadios = Array.from(groups[1].querySelectorAll('.el-radio-button'))
      .map(b => (b.innerText || '').replace(/\s+/g, ' ').trim());
  }
  out.scope = (document.querySelector('.flux-switch__scope') || {}).innerText || null;
  const act = Array.from(document.querySelectorAll('.flux-stat__value')).map(e => e.innerText.trim());
  out.stats = act;
  return JSON.stringify(out);
})()