(() => {
  const dr = document.querySelector('.Drawer_outerWrapper_5-120-1');
  if (!dr) return 'no drawer';
  const svg = document.querySelector('[data-testid="beast-core-icon-close"]');
  if (!svg) return 'no close svg';
  const r = svg.getBoundingClientRect();
  const path = svg.querySelector('path');
  const target = path || svg;
  const hit = (el) => {
    el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2, pointerId: 1, isPrimary: true }));
    el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 }));
    el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2, pointerId: 1, isPrimary: true }));
    el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 }));
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 }));
  };
  // 逐层往上试，直到抽屉真的关掉
  let el = svg, tried = [];
  while (el) {
    hit(el);
    tried.push(el.tagName + '.' + String(el.className).slice(0, 40));
    if (!document.querySelector('.Drawer_outerWrapper_5-120-1')) break;
    el = el.parentElement;
  }
  return JSON.stringify({ tried, closed: !document.querySelector('.Drawer_outerWrapper_5-120-1') });
})()