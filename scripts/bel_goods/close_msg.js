(() => {
  // 关掉全店消息弹窗（若有）
  const closers = Array.from(document.querySelectorAll('[class*=close], [class*=Close], svg[class*=close]'))
    .filter(e => e.offsetParent !== null);
  let closed = 0;
  closers.slice(0, 6).forEach(e => { try { e.click(); closed++; } catch (x) {} });

  // 货号类型下拉：当前是 SKU，切成 SKC
  const typeInput = Array.from(document.querySelectorAll('input')).find(
    el => el.offsetParent !== null && (el.value === 'SKU' || el.value === 'SPU')
  );

  return JSON.stringify({
    closedCount: closed,
    typeValue: typeInput ? typeInput.value : null,
    placeholder: typeInput ? typeInput.closest('div[class*=IPT]').parentElement.innerText.replace(/\n/g, ' ').slice(0, 80) : null
  });
})()