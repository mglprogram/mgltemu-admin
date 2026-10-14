(() => {
  const el = document.querySelector('input[placeholder="多个货号以空格，逗号分隔"]');
  const block = el.closest('div[class*=inputBlockCell]');
  const row = block.closest('div[class*=row_field]');
  const sel = row.querySelector('input[readonly]');
  sel.click();
  return 'clicked dropdown, current=' + sel.value;
})()