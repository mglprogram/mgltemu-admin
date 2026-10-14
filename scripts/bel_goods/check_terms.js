(() => {
  const out = {};

  // 1) 勾条款：input 尺寸 0/隐藏，必须点可见的 LABEL/DIV 外层
  const cb = document.querySelector('input[type=checkbox]');
  if (!cb) return JSON.stringify({ ok: false, msg: 'no checkbox' });
  out.alreadyChecked = cb.checked;
  if (!cb.checked) {
    let lbl = cb.closest('label');
    if (!lbl) {
      // 向上找带 CBX_ 类名的可点外层
      let p = cb.parentElement;
      for (let i = 0; i < 5 && p; i++) {
        if (/CBX_|checkbox/i.test(String(p.className))) { lbl = p; break; }
        p = p.parentElement;
      }
    }
    const target = lbl || cb.parentElement;
    target.click();
    out.checkedBy = String(target.className).slice(0, 50);
  }
  out.nowChecked = document.querySelector('input[type=checkbox]').checked;
  return JSON.stringify(out);
})()