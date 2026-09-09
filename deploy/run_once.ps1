# TrendRadar 定时任务单轮运行脚本（被计划任务调用）
# 职责：加载项目根目录 secrets.env 的真实密钥 -> 注入进程环境变量 -> 运行一轮主流程 -> 落盘日志
$ErrorActionPreference = "Continue"

# 项目根目录 = deploy/ 的上一级
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# --- 加载本地密钥（secrets.env 已被 .gitignore 排除，不会进 git） ---
$secretsPath = Join-Path $root "secrets.env"
if (Test-Path $secretsPath) {
    Get-Content $secretsPath | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $i = $line.IndexOf("=")
            $key = $line.Substring(0, $i).Trim()
            $val = $line.Substring($i + 1).Trim()
            [Environment]::SetEnvironmentVariable($key, $val, "Process")
        }
    }
} else {
    Write-Output "[deploy] 未找到 secrets.env，将仅使用 config.yaml / 系统环境变量中的配置"
}

# 日志/输出目录
$logDir = Join-Path $root "output"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logFile = Join-Path $logDir "trendradar_task.log"

# 强制子进程 UTF-8 输出，规避 GBK 编码问题（与 __main__.py 的 reconfigure 双保险）
$env:PYTHONIOENCODING = "utf-8"

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] === TrendRadar 一轮运行开始 ===" | Out-File $logFile -Append -Encoding utf8
& $python -m trendradar *>> $logFile
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] === 运行结束，退出码 $LASTEXITCODE ===" | Out-File $logFile -Append -Encoding utf8

exit $LASTEXITCODE
