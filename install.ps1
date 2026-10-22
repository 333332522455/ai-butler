#requires -Version 5.1
<#
AI 管家安装脚本：全局呼出条(hotkey.py) + 截屏守卫(window_shot.py) + 操控气泡(agent-bubble.py)

特点：
  * 探测而不是硬编码（Python / pythonw / Pillow / DSH 端口）；
  * 幂等：重复跑不会重复建任务、不会复制一堆副本；
  * 热键占用检测（被别的程序占了会明确告诉你）；
  * 登录自启走「计划任务 -> wscript //B -> vbs -> pythonw」，全程无黑框；
  * 配套 uninstall.ps1 可完整卸载。

用法（先看再跑，不要 irm|iex）：
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -DryRun
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -InstallDir "D:\tools\ai-butler" -NoAutostart
#>
[CmdletBinding()]
param(
  [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'ai-butler'),
  [string]$PythonPath = '',
  [string]$Hotkey = 'ctrl+alt+space',
  [string]$InboxUrl = 'http://127.0.0.1:43120/wechat-bridge/inbox',
  [switch]$NoAutostart,
  [switch]$NoStart,
  [switch]$DryRun,
  [switch]$Force,
  [string]$TaskName = 'ai-butler-autostart'
)

$ErrorActionPreference = 'Stop'
$script:Warn = 0
function Step($t) { Write-Host ''; Write-Host ('== ' + $t) -ForegroundColor Cyan }
function Ok($t)   { Write-Host ('   [OK] ' + $t) -ForegroundColor Green }
function Warn($t) { Write-Host ('   [!]  ' + $t) -ForegroundColor Yellow; $script:Warn++ }
function Die($t)  { Write-Host ('   [X]  ' + $t) -ForegroundColor Red; exit 2 }

Write-Host ''
Write-Host 'AI 管家安装程序' -ForegroundColor Cyan
Write-Host ('目标目录: ' + $InstallDir)
if ($DryRun) { Write-Host '模式: 演练（只体检，不写盘、不建任务、不启动）' -ForegroundColor Yellow }

# ---------- 1. Python ----------
Step '1/8 探测 Python'
$pyCands = @()
if ($PythonPath) { $pyCands += ,@($PythonPath) }
$pyCands += ,@('py', '-3')
$pyCands += ,@('python')
$pyCands += ,@('python3')
$py = $null
foreach ($c in $pyCands) {
  $exe = $c[0]
  $pre = @()
  if ($c.Count -gt 1) { $pre = $c[1..($c.Count - 1)] }
  # 用十六进制传路径：控制台代码页与 Python 输出编码不一致时（中文用户名/目录），
  # 直接把路径当文本抓回来会变乱码，Test-Path 随即失败。
  try { $out = & $exe @pre -c "import sys;print(sys.executable.encode('utf-8').hex())" 2>$null } catch { continue }
  if ($LASTEXITCODE -eq 0 -and $out) {
    $h = ($out | Select-Object -Last 1).Trim()
    if ($h -match '^[0-9a-fA-F]+$' -and ($h.Length % 2) -eq 0) {
      $bytes = New-Object 'byte[]' ($h.Length / 2)
      for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = [Convert]::ToByte($h.Substring($i * 2, 2), 16) }
      $py = [System.Text.Encoding]::UTF8.GetString($bytes)
      break
    }
  }
}
if (-not $py) { Die '找不到 Python。请装 Python 3.9+（安装时勾 Add to PATH），或用 -PythonPath 指定 python.exe' }
if (-not (Test-Path $py)) { Die ('Python 路径不存在: ' + $py) }
$ver = (& $py -c "import sys;print('%d.%d' % sys.version_info[:2])").Trim()
if ([version]$ver -lt [version]'3.9') { Die ('Python 版本太低: ' + $ver + '，需要 3.9+') }
Ok ('解释器 ' + $py + '  (Python ' + $ver + ')')

# ---------- 2. Pillow ----------
Step '2/8 依赖检查 (Pillow：截屏用)'
$pil = & $py -c 'import PIL;print(PIL.__version__)' 2>$null
if ($LASTEXITCODE -ne 0 -or -not $pil) {
  Warn 'Pillow 未安装 —— 呼出条能用，但「呼出时截屏」不可用'
  if (-not $DryRun) {
    Write-Host '   正在安装 Pillow ...'
    & $py -m pip install --quiet --disable-pip-version-check pillow
    if ($LASTEXITCODE -ne 0) { Warn 'pip install pillow 失败，请手动安装（截屏功能将不可用）' } else { Ok 'Pillow 已安装' }
  }
} else {
  Ok ('Pillow ' + ($pil | Select-Object -Last 1).Trim())
}

# ---------- 3. 源文件 ----------
Step '3/8 检查安装源'
$srcTools = Join-Path $PSScriptRoot 'tools'
if (-not (Test-Path $srcTools)) { Die ('找不到 ' + $srcTools + '，请在仓库根目录运行 install.ps1') }
$need = @('hotkey.py', 'window_shot.py', 'agent-bubble.py', 'desktop.py', 'dev.py', 'license.py', 'wizard.py', 'abconfig.py', 'shot-blocklist.json')
$missing = @()
foreach ($f in $need) { if (-not (Test-Path (Join-Path $srcTools $f))) { $missing += $f } }
if ($missing.Count -gt 0) { Die ('安装源缺文件: ' + ($missing -join ', ')) }
Ok ($need.Count.ToString() + ' 个文件齐全')

# ---------- 4. 热键 ----------
Step ('4/8 热键可用性 (' + $Hotkey + ')')
$probe = Join-Path $env:TEMP 'ai-butler-hotkey-probe.py'
$probeCode = @'
import ctypes, sys
from ctypes import wintypes
MODS = {'ctrl': 0x0002, 'alt': 0x0001, 'shift': 0x0004, 'win': 0x0008}
VK = {'space': 0x20, 'enter': 0x0D, 'tab': 0x09, 'esc': 0x1B}
for i in range(1, 13):
    VK['f%d' % i] = 0x6F + i
for ch in 'abcdefghijklmnopqrstuvwxyz':
    VK[ch] = ord(ch.upper())
spec = (sys.argv[1] if len(sys.argv) > 1 else '').lower().replace(' ', '')
mods = 0
vk = 0
for part in spec.split('+'):
    if part in MODS:
        mods |= MODS[part]
    elif part in VK:
        vk = VK[part]
    else:
        print('BAD ' + part)
        sys.exit(3)
if not mods or not vk:
    print('BAD 需要至少一个修饰键(ctrl/alt/shift/win) + 一个主键')
    sys.exit(3)
u = ctypes.windll.user32
u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
u.RegisterHotKey.restype = wintypes.BOOL
ok = u.RegisterHotKey(None, 0xB0FF, mods, vk)
if ok:
    u.UnregisterHotKey(None, 0xB0FF)
print('FREE' if ok else 'BUSY')
'@
Set-Content -Path $probe -Value $probeCode -Encoding UTF8
$hkOut = & $py $probe $Hotkey
$hkCode = $LASTEXITCODE
if ($hkCode -eq 3) { Die ('热键写法不对: ' + $Hotkey + '（示例: ctrl+alt+space）') }
if (($hkOut | Out-String) -match 'BUSY') {
  Warn ('热键 ' + $Hotkey + ' 已被占用：可能是本工具旧实例或别的软件在用它。继续装会导致呼出条抢不到热键')
} else {
  Ok '热键空闲'
}

# ---------- 5. 已有实例 ----------
Step '5/8 已有实例检查'
$running = @()
try {
  $running = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -and ($_.CommandLine -like '*hotkey.py*' -or $_.CommandLine -like '*agent-bubble.py*') })
} catch { }
if ($running.Count -gt 0) {
  foreach ($p in $running) {
    $script = '?'
    if ($p.CommandLine -match '([^\\/]+)\.py') { $script = $Matches[1] }
    Warn ('已在运行: pid ' + $p.ProcessId + '  ' + $script)
  }
  if ($Force) {
    if (-not $DryRun) {
      foreach ($p in $running) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
      Start-Sleep -Milliseconds 400
      Ok '已结束旧实例（-Force）'
    }
  } else {
    Warn '旧实例还在跑。要换成本次安装的版本，加 -Force；或装完自己重启一次'
  }
} else {
  Ok '没有旧实例在跑'
}

# ---------- 6. 复制 + 配置 + 启动器 ----------
Step '6/8 落地文件'
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py; Warn '没找到 pythonw.exe，将用 python.exe（可能闪一下黑框）' }
$launcherDir = Join-Path $InstallDir 'launchers'
$logsDir = Join-Path $InstallDir 'logs'

function New-Launcher([string]$name, [string]$scriptName) {
  $target = Join-Path (Join-Path $InstallDir 'tools') $scriptName
  $lines = @(
    'Set sh = CreateObject("WScript.Shell")',
    'Set env = sh.Environment("Process")',
    ('env("AI_BUTLER_HOME") = "' + $InstallDir + '"'),
    ('env("AI_BUTLER_HOTKEY") = "' + $Hotkey + '"'),
    ('env("AI_BUTLER_INBOX") = "' + $InboxUrl + '"'),
    ('sh.Run Chr(34) & "' + $pyw + '" & Chr(34) & " " & Chr(34) & "' + $target + '" & Chr(34), 0, False')
  )
  $path = Join-Path $launcherDir $name
  $utf16 = New-Object System.Text.UnicodeEncoding($false, $true)
  [System.IO.File]::WriteAllText($path, ($lines -join ([char]13 + [char]10)), $utf16)
  return $path
}

if ($DryRun) {
  Ok ('演练：将复制 tools 到 ' + (Join-Path $InstallDir 'tools'))
  Ok ('演练：将写 config.json / launchers / logs 到 ' + $InstallDir)
  $hotkeyVbs = Join-Path $launcherDir 'start-hotkey-hidden.vbs'
  $bubbleVbs = Join-Path $launcherDir 'start-bubble-hidden.vbs'
} else {
  New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir 'tools') | Out-Null
  New-Item -ItemType Directory -Force -Path $launcherDir | Out-Null
  New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
  Copy-Item -Path (Join-Path $srcTools '*') -Destination (Join-Path $InstallDir 'tools') -Force
  $cfg = [ordered]@{
    name      = 'ai-butler'
    version   = '1.0'
    installed = (Get-Date).ToString('s')
    python    = $py
    pythonw   = $pyw
    installDir = $InstallDir
    hotkey    = $Hotkey
    inbox     = $InboxUrl
    taskName  = $TaskName
    shot      = $true
  }
  ($cfg | ConvertTo-Json) | Set-Content -Path (Join-Path $InstallDir 'config.json') -Encoding UTF8
  # 首次运行向导（任务书 9.3）：把配置规范化一次，热键写错在这里就能发现，不留到运行时
  $wiz = Join-Path $InstallDir 'tools\wizard.py'
  if (Test-Path $wiz) {
    & $py $wiz --yes --no-license --hotkey $Hotkey --inbox $InboxUrl *> $null
    if ($LASTEXITCODE -eq 0) { Ok '首次运行向导：config.json 已规范化' }
    else { Warn ('首次运行向导返回 ' + $LASTEXITCODE + '，配置可能没写全，可手动跑 python "' + $wiz + '"') }
  } else { Warn '缺少 wizard.py，跳过首次运行向导' }
  $hotkeyVbs = New-Launcher 'start-hotkey-hidden.vbs' 'hotkey.py'
  $bubbleVbs = New-Launcher 'start-bubble-hidden.vbs' 'agent-bubble.py'
  Ok ('已复制 ' + $need.Count + ' 个文件到 ' + (Join-Path $InstallDir 'tools'))
  Ok ('已生成启动器 ' + $launcherDir)
}

# ---------- 7. 自启 ----------
Step '7/8 登录自启（计划任务）'
if ($NoAutostart) {
  Ok '按参数跳过（-NoAutostart）'
} elseif ($DryRun) {
  Ok ('演练：将注册计划任务 ' + $TaskName + ' -> wscript //B 两个 vbs')
} else {
  try {
    $wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $actions = @(
      (New-ScheduledTaskAction -Execute $wscript -Argument ('//B "' + $hotkeyVbs + '"')),
      (New-ScheduledTaskAction -Execute $wscript -Argument ('//B "' + $bubbleVbs + '"'))
    )
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $settings.ExecutionTimeLimit = 'PT0S'
    Register-ScheduledTask -TaskName $TaskName -Action $actions -Trigger $trigger -Settings $settings -Force | Out-Null
    Ok ('已注册计划任务 ' + $TaskName + '（隐藏窗口，登录时自动启动）')
  } catch {
    Warn ('注册计划任务失败: ' + $_.Exception.Message + '  —— 可以手动跑 ' + $hotkeyVbs)
  }
}

# ---------- 8. 启动 ----------
Step '8/8 立即启动'
if ($NoStart -or $DryRun) {
  Ok '按参数跳过启动'
} else {
  $wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
  Start-Process -FilePath $wscript -ArgumentList '//B', ('"' + $hotkeyVbs + '"') -WindowStyle Hidden
  Start-Process -FilePath $wscript -ArgumentList '//B', ('"' + $bubbleVbs + '"') -WindowStyle Hidden
  Start-Sleep -Milliseconds 1500
  $alive = @()
  try {
    $alive = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
      Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + $InstallDir + '\tools\hotkey.py*') })
  } catch { }
  if ($alive.Count -gt 0) {
    Ok ('呼出条已启动 (pid ' + $alive[0].ProcessId + ')')
  } else {
    Warn ('没看到 hotkey.py 进程，请查看日志 ' + (Join-Path $logsDir 'hotkey.log'))
  }
}

# ---------- 授权状态（离线激活码，任务书 9.5）----------
$licTool = Join-Path $InstallDir 'tools\license.py'
$licActivated = $false
$machineCode = ''
if (Test-Path $licTool) {
  try {
    $machineCode = ((& $py $licTool code) -join '').Trim()
    & $py $licTool check *> $null
    $licActivated = ($LASTEXITCODE -eq 0)
  } catch { }
}
if ($machineCode -match '^(....)(....)(....)(....)$') { $machineShow = '$1-$2-$3-$4' } else { $machineShow = $machineCode }
if ($licActivated) {
  Ok ('授权：已激活（本机机器码 ' + $machineShow + '）')
} elseif ($machineCode) {
  Warn ('授权：未激活。把机器码 ' + $machineShow + ' 发给作者换激活码，再运行：python "' + $licTool + '" activate XXXX-XXXX-XXXX-XXXX')
} else {
  Warn '授权：读不到机器码（tools\license.py 缺失？）'
}
Write-Host ''
Write-Host '安装完成。' -ForegroundColor Green
Write-Host ('  安装目录 : ' + $InstallDir)
Write-Host ('  热键     : ' + $Hotkey + '  (唤起后输入，回车发给 DSH；Esc 关闭)')
Write-Host ('  投递地址 : ' + $InboxUrl)
Write-Host ('  日志     : ' + (Join-Path $logsDir 'hotkey.log'))
Write-Host ('  截图     : ' + (Join-Path $logsDir 'shots'))
Write-Host ('  授权     : ' + $(if ($licActivated) { '已激活 ' + $machineShow } else { '未激活 ' + $machineShow }))
Write-Host ('  改配置   : python "' + (Join-Path $InstallDir 'tools\wizard.py') + '"（热键/投递地址/截图开关，可单独重跑）')
Write-Host ('  卸载     : powershell -NoProfile -File "' + $PSScriptRoot + '\uninstall.ps1" -InstallDir "' + $InstallDir + '"')
if ($script:Warn -gt 0) {
  Write-Host ('  注意：有 ' + $script:Warn + ' 条警告（见上面 [!]）') -ForegroundColor Yellow
}
exit 0
