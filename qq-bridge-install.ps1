# dsh-qq-bridge 一键安装（Windows PowerShell）
#   一键：irm https://333332522455.github.io/ai-butler/qq-bridge-install.ps1 | iex
#   可选参数：-Dir <装到哪> -Version <版本> -ZipPath <本地zip> -NodePath <node.exe 路径>
[CmdletBinding()]
param(
  [string]$Dir = (Join-Path $HOME 'dsh-qq-bridge'),
  [string]$Version = '1.0.1',
  [string]$ZipPath = '',
  [string]$NodePath = ''
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$name = "dsh-qq-bridge-$Version.zip"
$urls = @(
  "https://github.com/333332522455/ai-butler/releases/latest/download/$name",
  "https://333332522455.github.io/ai-butler/qq-bridge/$name",
  "https://raw.githubusercontent.com/333332522455/ai-butler/main/qq-bridge/$name",
  "https://cdn.jsdelivr.net/gh/333332522455/ai-butler@main/qq-bridge/$name"
)
function Say($m) { Write-Host "  $m" }
function Fail($m) { Write-Host ''; Write-Host "  [X] $m" -ForegroundColor Red; exit 1 }

Write-Host ''
Write-Host "  dsh-qq-bridge $Version 一键安装" -ForegroundColor Cyan
Write-Host '  --------------------------------------------'

# ---- 1. 找 Node.js ----
$nodeExe = $null
if ($NodePath) {
  if (Test-Path $NodePath) { $nodeExe = $NodePath } else { Fail "指定的 node 不存在：$NodePath" }
} else {
  $cmd = Get-Command node -ErrorAction SilentlyContinue
  if ($cmd) { $nodeExe = $cmd.Source }
  if (-not $nodeExe) {
    $cands = @(
      (Join-Path $env:ProgramFiles 'nodejs\node.exe'),
      (Join-Path $env:LOCALAPPDATA 'Programs\nodejs\node.exe'),
      (Join-Path $env:APPDATA 'nvm\node.exe'),
      'C:\ProgramData\chocolatey\bin\node.exe',
      (Join-Path $HOME 'scoop\apps\nodejs\current\node.exe')
    )
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { $nodeExe = $c; break } }
  }
}
if (-not $nodeExe) {
  Fail '没找到 Node.js。先装 Node 18+（https://nodejs.org/），装完重开 PowerShell 再跑；或用 -NodePath "C:\path\to\node.exe" 指定。'
}
$nv = (& $nodeExe -v).Trim()
$major = [int]($nv.TrimStart('v').Split('.')[0])
Say "Node.js $nv  ($nodeExe)"
if ($major -lt 18) { Fail "Node 版本太低（$nv），需要 18 或更新。" }

# ---- 2. 取安装包 ----
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('qqb-' + [Guid]::NewGuid().ToString('N') + '.zip')
if ($ZipPath) {
  if (-not (Test-Path $ZipPath)) { Fail "本地包不存在：$ZipPath" }
  Copy-Item $ZipPath $tmp
  Say "使用本地包 $ZipPath"
} else {
  $ok = $false
  foreach ($u in $urls) {
    try {
      Say "下载 $u"
      Invoke-WebRequest -Uri $u -OutFile $tmp -UseBasicParsing -TimeoutSec 90
      if ((Get-Item $tmp).Length -gt 10000) { $ok = $true; break }
    } catch { Say '  这个源不通，换下一个' }
  }
  if (-not $ok) { Fail '下载失败。可手动下载 zip 后再跑：.\qq-bridge-install.ps1 -ZipPath <zip 路径>' }
}
Say ('包大小 ' + [math]::Round((Get-Item $tmp).Length / 1KB, 1) + ' KB')

# ---- 3. 解压（保留已有 .state 配置）----
$stateDir = Join-Path $Dir '.state'
$stateBak = $null
if (Test-Path $stateDir) {
  $stateBak = Join-Path ([IO.Path]::GetTempPath()) ('qqb-state-' + [Guid]::NewGuid().ToString('N'))
  Copy-Item $stateDir $stateBak -Recurse -Force
}
if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir -Force | Out-Null }
Get-ChildItem -Path $Dir -Force | Where-Object { $_.Name -ne '.state' } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -Path $tmp -DestinationPath $Dir -Force
Remove-Item $tmp -Force
if ($stateBak) {
  $inner = Join-Path $stateBak '.state'
  if (Test-Path $inner) {
    if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }
    Copy-Item (Join-Path $inner '*') $stateDir -Recurse -Force
  }
  Remove-Item $stateBak -Recurse -Force
}
Say "已解压到 $Dir"

# ---- 4. 记住 node 路径、生成双击可运行的入口 ----
if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }
$utf8noBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $stateDir 'node.path'), $nodeExe, $utf8noBom)
Set-Location $Dir
$cli = Join-Path $Dir 'bin\qq-bridge.mjs'
if (Test-Path $cli) {
  $prevEa = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  for ($try = 1; $try -le 2; $try++) {
    try {
      $initOut = & $nodeExe $cli init 2>&1
      Say (($initOut | Out-String).Trim())
      break
    } catch {
      if ($try -eq 2) { Say "  配置初始化跳过（稍后运行 check.cmd 可重试）：$($_.Exception.Message) " }
      else { Start-Sleep -Milliseconds 500 }
    }
  }
  $ErrorActionPreference = $prevEa
}
$nl = [Environment]::NewLine
$cmdStart = @('@echo off', 'setlocal', 'chcp 65001 >nul', 'cd /d "%~dp0"', '"NODEEXE" "%~dp0bin\qq-bridge.mjs" start %*') -join $nl
$cmdCheck = @('@echo off', 'setlocal', 'chcp 65001 >nul', 'cd /d "%~dp0"', '"NODEEXE" "%~dp0bin\qq-bridge.mjs" check %*', 'pause') -join $nl
[IO.File]::WriteAllText((Join-Path $Dir 'start.cmd'), $cmdStart.Replace('NODEEXE', $nodeExe), $utf8noBom)
[IO.File]::WriteAllText((Join-Path $Dir 'check.cmd'), $cmdCheck.Replace('NODEEXE', $nodeExe), $utf8noBom)

Write-Host ''
Write-Host '  装好了。' -ForegroundColor Green
Write-Host '  --------------------------------------------'
Write-Host '  1) 去 https://q.qq.com/ 建一个机器人，拿 AppID / AppSecret'
Write-Host "  2) 填配置：notepad $Dir\.state\qq.json"
Write-Host "  3) 自检  ：$Dir\check.cmd"
Write-Host "  4) 起桥  ：$Dir\start.cmd"
Write-Host ''
Write-Host "  说明书：$Dir\README.md"
Write-Host ''
