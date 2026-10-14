(() => {
  const gs = document.querySelectorAll('.flux-switch .el-radio-group');
  const btns = gs[1].querySelectorAll('.el-radio-button');
  btns[1].querySelector('input').click();   // 动力不足
  return 'clicked weak';
})()