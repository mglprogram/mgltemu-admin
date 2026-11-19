(() => {
  const pg = document.querySelector('[class*=PGT_outerWrapper],[class*=pagination-wrapper]');
  return JSON.stringify({
    pager: pg ? (pg.innerText || '').replace(/[\s]+/g, ' ').slice(0, 140) : null,
    rowsDom: document.querySelectorAll('tr').length,
    total: (document.body.innerText.match(/共有\s*(\d+)\s*条/) || [])[1] || null,
    pagerItems: Array.from(document.querySelectorAll('[class*=PGT_pagerItem],li[class*=pager]'))
      .map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 12),
  });
})()