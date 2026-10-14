(() => {
  const el = document.querySelector('input[placeholder="多个货号以空格，逗号分隔"]');
  if (!el) return JSON.stringify({err: 'no input'});

  // 受控组件：必须用 React 认识的原生 setter 写入，再派发 input 事件
  const proto = Object.getPrototypeOf(el);
  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
  const setter = desc && desc.set;

  const codes = window.__CODES__ || '';
  if (setter) setter.call(el, codes);
  else el.value = codes;

  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));

  return JSON.stringify({after: el.value.slice(0, 60), len: el.value.length});
})()