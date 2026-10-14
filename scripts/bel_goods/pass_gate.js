(() => {
  // 过密码门禁（若在）
  const gp = document.getElementById('gp');
  if (gp) {
    gp.value = window.__PW__;
    document.getElementById('gb').click();
  }
  return JSON.stringify({ submitted: !!gp });
})()