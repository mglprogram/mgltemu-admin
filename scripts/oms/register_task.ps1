# 注册「领星 OMS 订单同步」计划任务（每天 15:00 中国时间 = 00:00 太平洋时间）
#
#   用法（管理员/普通用户均可，注册当前用户的任务）：
#       powershell -NoProfile -ExecutionPolicy Bypass -File scripts\oms\register_task.ps1
#
# 为什么是 15:00：做的是美国站，业务口径是「美国前一天」。
#   太平洋时间是美国最晚跨 0 点的时区 —— 00:00 PT 时全美都已过完前一天，
#   所以 15:00 中国时间（PDT=UTC-7）触发，抓到的「美国前一天」是完整的。
#   脚本侧再用 us_day_bounds_cn() 把「美国某天」换算成接口要的中国时间窗口
#   （接口的 orderCreateTime 实测是中国时间）。
#
# 两个坑：
#   1) 不能只用 New-ScheduledTaskSettingsSet：Windows PowerShell 5.1 没有
#      -DisallowStartIfOnBatteries 参数。所以直接**复制**一个既有任务的 XML，
#      设置/动作完全一致，只换描述与命令。
#   2) 不能在 XML 里改 StartBoundary：PS 把 Triggers.CalendarTrigger 暴露成集合，
#      对集合属性赋值**不报错也不生效**（实测 NextRunTime 还停在源任务的 08:00）。
#      必须注册完再用 Set-ScheduledTask -Trigger 显式设。
#      ⚠ 另外别用反引号续行，实测会被拆成两条命令报 "-Trigger 不是 cmdlet"。
$ErrorActionPreference = 'Stop'

$name = 'TEMU-OMS-Orders-Daily'
$src  = 'TEMU-Goods-OnSale-Daily'      # 模板任务（设置与它完全一致）
$repo = 'D:\AI-workplace\TEMU'
$vbs  = 'C:\Users\saing\.agents\skills\scheduled-tasks\scripts\run_hidden.vbs'
$cmd  = Join-Path $repo 'scripts\run_oms_orders.cmd'
$at   = [datetime]'2026-10-15T15:00:00'
$desc = '每天 15:00 中国时间（= 00:00 太平洋时间，全美已过完前一天）从领星 OMS(xlwms) 开放接口拉取「美国前一天」的 BHY/BEL 出库单（含州-市-邮编/产品/跟踪号）→ 更新数据源 out\oms\oms_orders.jsonl → 并进「订单分布」与大屏'

if (-not (Test-Path $vbs)) { throw "run_hidden.vbs 不存在: $vbs" }
if (-not (Test-Path $cmd)) { throw "cmd 不存在: $cmd" }

[xml]$x = Export-ScheduledTask -TaskName $src
$x.Task.RegistrationInfo.Description = $desc
$x.Task.RegistrationInfo.URI = "\$name"
$x.Task.Actions.Exec.Arguments = ('//nologo "{0}" "cmd /c ""{1}"""' -f $vbs, $cmd)

if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Output "已删除同名旧任务"
}
Register-ScheduledTask -TaskName $name -Xml $x.OuterXml | Out-Null
Set-ScheduledTask -TaskName $name -Trigger (New-ScheduledTaskTrigger -Daily -At $at) | Out-Null

$t = Get-ScheduledTask -TaskName $name
if ($t.Triggers.StartBoundary -notmatch 'T15:00:00') {
    throw "触发器没设上：$($t.Triggers.StartBoundary)（见文件头坑 2）"
}
Write-Output "== 注册完成 =="
$t | Select-Object TaskName, State | Format-Table -AutoSize
$t.Triggers | Select-Object StartBoundary, Enabled | Format-List
$t.Actions | Select-Object Execute, Arguments | Format-List
Get-ScheduledTaskInfo -TaskName $name | Select-Object NextRunTime | Format-List
