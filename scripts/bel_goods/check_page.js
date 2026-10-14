(() => {
  const url = location.href;
  const out = { url, title: document.title };
  // 授权确认框判据：条款 checkbox + 授权按钮
  out.hasCheckbox = !!document.querySelector('input[type=checkbox], [class*=CBX_]');
  out.checkboxCount = document.querySelectorAll('input[type=checkbox]').length;
  const btns = Array.from(document.querySelectorAll('button,a,[class*=btn],[class*=Btn],[class*=Button]'))
    .filter(e => e.offsetParent !== null && (e.innerText || '').trim())
    .map(e => ({ tag: e.tagName, txt: (e.innerText || '').trim().slice(0, 30), cls: String(e.className).slice(0, 60) }));
  out.buttons = btns.slice(0, 12);
  out.body = (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 260);
  return JSON.stringify(out);
})()