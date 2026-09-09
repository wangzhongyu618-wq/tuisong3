# TrendRadar Windows 计划任务安装/卸载脚本
# 用法：
#   安装（默认每 30 分钟一轮）：  powershell -ExecutionPolicy Bypass -File install_schedule.ps1
#   自定义间隔：                  powershell -ExecutionPolicy Bypass -File install_schedule.ps1 -IntervalMinutes 60
#   卸载：                        powershell -ExecutionPolicy Bypass -File install_schedule.ps1 -Remove
param(
    [int]$IntervalMinutes = 30,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$taskName = "TrendRadar"
$scriptPath = Join-Path $PSScriptRoot "run_once.ps1"

if ($Remove) {
    schtasks /delete /tn $taskName /f 2>$null
    Write-Host "已删除计划任务 $taskName"
    exit 0
}

if (-not (Test-Path $scriptPath)) {
    Write-Error "未找到运行脚本: $scriptPath"
    exit 1
}

# 首次触发放在 2 分钟后，之后按固定间隔重复（重复周期 10 年，覆盖日常使用）
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
# 开机/电池模式下照常运行；错过触发点（休眠/关机）恢复后补跑；同一时间仅允许一个实例
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "TrendRadar 热点聚合定时抓取（雪球+热榜+AI 分析）" -Force | Out-Null

Write-Host "已注册计划任务 $taskName：每 $IntervalMinutes 分钟运行一轮"
Write-Host "首次运行: $((Get-Date).AddMinutes(2).ToString('HH:mm')) 左右"
Write-Host "日志位置: output\trendradar_task.log"
Write-Host "手动触发: schtasks /run /tn $taskName"
Write-Host "查看状态: schtasks /query /tn $taskName /v"
Write-Host ""
Write-Host "提醒：雪球抓取需要已登录会话（本任务以当前 Windows 用户身份运行）。"
