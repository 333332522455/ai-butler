#requires -Version 5.1
<#
AI 管家卸载脚本：结束呼出条/气泡进程 -> 注销计划任务 -> 删除安装目录。

用法：
  powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1 -DryRun     # 只报告要动什么
  powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1 -KeepFiles  # 只停进程+注销任务，留文件
#>
[CmdletBinding()]
param(
  [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'ai-butler'),
  [string]$TaskName = 'ai-butler-autostart',
  [switch]$KeepFiles,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$script:Warn = 0
function Step($t) { Write-Host ''; Write-Host ('== ' + $t) -ForegroundColor Cyan }
function Ok($t)   { Write-Host ('   [OK] ' + $t) -ForegroundColor Green }
function Warn($t) { Write-Host ('   [!]  ' + $t) -ForegroundColor Yellow; $script:Warn++ }

Write-Host ''
Write-Host 'AI 管家卸载程序' -ForegroundColor Cyan
Write-Host ('目标目录: ' + $InstallDir)
if ($DryRun) { Write-Host '模式: 演练（只报告，不结束进程、不注销任务、不删文件）' -ForegroundColor Yellow }

# 安全阀：拒绝删盘根 / 太短的路径
if (-not $KeepFiles) {
  $trimmed = $InstallDir.TrimEnd('\\')
  if ($trimmed -match '^[A-Za-z]:$' -or $trimmed.Length -lt 6) {
    Write-Host ('   [X] 拒绝删除可疑路径: ' + $InstallDir) -ForegroundColor Red
    exit 2
  }
}

# ---------- 1. 进程 ----------
Step '1/3 结束进程'
$procs = @()
try {
  $procs = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -and ($_.CommandLine -like ('*' + $InstallDir + '*') -or
      ($_.CommandLine -like '*hotkey.py*' -and $_.CommandLine -like '*agent-bubble.py*')) })
} catch { }
if ($procs.Count -eq 0) {
  Ok '没有找到本工具相关进程'
} else {
  foreach ($p in $procs) {
    if ($DryRun) {
      Ok ('演练：将结束 pid ' + $p.ProcessId)
    } else {
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
      Ok ('已结束 pid ' + $p.ProcessId)
    }
  }
}

# ---------- 2. 计划任务 ----------
Step '2/3 注销登录自启'
try {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
} catch { $task = $null }
if ($null -eq $task) {
  Ok ('没有计划任务 ' + $TaskName + '（跳过）')
} elseif ($DryRun) {
  Ok ('演练：将注销计划任务 ' + $TaskName)
} else {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Ok ('已注销计划任务 ' + $TaskName)
}

# ---------- 3. 文件 ----------
Step '3/3 删除安装文件'
if ($KeepFiles) {
  Ok ('按参数保留文件: ' + $InstallDir)
} elseif (-not (Test-Path $InstallDir)) {
  Ok '安装目录不存在（已干净）'
} elseif ($DryRun) {
  Ok ('演练：将删除 ' + $InstallDir)
} else {
  try {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
    Ok ('已删除 ' + $InstallDir)
  } catch {
    Warn ('删除失败: ' + $_.Exception.Message + '  —— 可能还有进程占着文件，关掉后重试')
  }
}

# ---------- 收尾 ----------
Step '收尾检查'
$left = @()
try {
  $left = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + $InstallDir + '*') })
} catch { }
if ($left.Count -gt 0) { Warn ('还有 ' + $left.Count + ' 个进程在跑') } else { Ok '进程已清空' }
try {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Warn ('计划任务 ' + $TaskName + ' 仍然存在') } else { Ok '计划任务已清空' }
} catch { }

Write-Host ''
Write-Host '卸载完成。' -ForegroundColor Green
if ($script:Warn -gt 0) { Write-Host ('  注意：有 ' + $script:Warn + ' 条警告（见上面 [!]）') -ForegroundColor Yellow }
exit 0
