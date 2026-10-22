# AI Butler bootstrap - animated edition (ASCII only, safe under irm | iex)
#
#   irm https://333332522455.github.io/ai-butler/bootstrap.ps1 | iex
#
# Env overrides:
#   AI_BUTLER_ZIP_URL       alternative package zip
#   AI_BUTLER_INSTALL_ARGS  extra args for install.ps1, e.g. -DryRun or -NoAutostart
#   AI_BUTLER_KEEP_TEMP     set to 1 to keep the extracted package
#   AI_BUTLER_PLAIN         set to 1 to disable the animation (CI / logging)
#   AI_BUTLER_ANIM          set to 1 to force the animation even when redirected

$ErrorActionPreference = 'Stop'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
try { [Net.ServicePointManager]::Expect100Continue = $false } catch {}

$repo = '333332522455/ai-butler'
if ($env:AI_BUTLER_ZIP_URL) { $zipUrl = $env:AI_BUTLER_ZIP_URL }
else { $zipUrl = 'https://codeload.github.com/' + $repo + '/zip/refs/heads/main' }

# animation only when we own a real console
$anim = $true
try { if ([Console]::IsOutputRedirected) { $anim = $false } } catch { $anim = $false }
if ($env:AI_BUTLER_PLAIN -eq '1') { $anim = $false }
if ($env:AI_BUTLER_ANIM -eq '1') { $anim = $true }
$CR = [char]13

function W([string]$t, [string]$c) {
  if ($c) { Write-Host $t -ForegroundColor $c -NoNewline } else { Write-Host $t -NoNewline }
}

function Bar([long]$done, [long]$total, [double]$secs, [bool]$final) {
  $w = 30
  if ($total -gt 0) { $pct = [int](100 * $done / $total) } else { $pct = 0 }
  if ($pct -gt 100) { $pct = 100 }
  $fill = [int]($w * $pct / 100)
  $bar = ("#" * $fill)
  if ($fill -lt $w) {
    $bar = $bar + ">"
    $bar = $bar + ("." * ($w - $fill - 1))
  }
  $mb = [math]::Round($done / 1MB, 2)
  $tm = [math]::Round($total / 1MB, 2)
  $speed = 0
  if ($secs -gt 0.2) { $speed = [int]($done / $secs / 1KB) }
  $eta = "--"
  if ($speed -gt 0 -and $total -gt 0) {
    $left = [math]::Max(0, $total - $done)
    $eta = ([int]($left / ($speed * 1KB))).ToString() + "s"
  }
  if ($anim) { Write-Host ($CR + "  ") -NoNewline }
  if ($final -or $pct -eq 100) { W $bar "Green" } else { W $bar "Cyan" }
  W (" " + $pct.ToString().PadLeft(3) + "%  ") "White"
  W ($mb.ToString() + "/" + $tm.ToString() + " MB  ") "DarkGray"
  W ($speed.ToString() + " KB/s  ETA " + $eta + "  ") "DarkGray"
  if ($anim) { W (" " * 6) "" }
}

function Spin([string]$label, [string]$frames, [int]$ms) {
  if (-not $anim) { return }
  $i = 0
  $end = (Get-Date).AddMilliseconds($ms)
  while ((Get-Date) -lt $end) {
    $ch = $frames.Substring($i % $frames.Length, 1)
    Write-Host ($CR + "  " + $label + " " + $ch) -ForegroundColor DarkYellow -NoNewline
    Start-Sleep -Milliseconds 70
    $i = $i + 1
  }
  Write-Host ($CR + "  " + $label + "  done" + " " * 8) -ForegroundColor Green
}

function Step([string]$n, [string]$t) {
  Write-Host ("  " + $n + " " + $t) -ForegroundColor Cyan
}

function Done([string]$t) {
  Write-Host ($CR + "  " + $t + " " * 10) -ForegroundColor Green
}

function DownloadPackage([string]$url, [string]$dest) {
  $attempt = 0
  while ($attempt -lt 3) {
    $attempt = $attempt + 1
    $rsp = $null
    $inS = $null
    $outS = $null
    try {
      if ($attempt -gt 1) {
        if ($anim) { W ($CR + "  retrying (" + $attempt + "/3) ..." + " " * 24) "Yellow" }
        Start-Sleep -Milliseconds 900
      }
      $req = [Net.HttpWebRequest]::Create($url)
      $req.Timeout = 20000
      $req.ReadWriteTimeout = 15000
      $req.UserAgent = 'ai-butler-bootstrap'
      $req.AllowAutoRedirect = $true
      $iar = $req.BeginGetResponse($null, $null)
      $frames = '|/-'
      $i = 0
      $t0 = (Get-Date)
      while (-not $iar.AsyncWaitHandle.WaitOne(80)) {
        $el = ((Get-Date) - $t0).TotalSeconds
        if ($el -gt 25) { try { $req.Abort() } catch {}; throw 'connect timeout (25s)' }
        if ($anim) {
          $ch = $frames.Substring($i % 3, 1)
          W ($CR + ("  connecting " + $ch + "  " + [int]$el + "s").PadRight(72)) "DarkYellow"
        }
        $i = $i + 1
      }
      $rsp = $req.EndGetResponse($iar)
      if ($anim) { Write-Host ($CR + ("  connected in " + [int]$el + "s, receiving").PadRight(72)) -ForegroundColor Green }
      $total = $rsp.ContentLength
      $inS = $rsp.GetResponseStream()
      $outS = [IO.File]::Create($dest)
      $buf = New-Object byte[] 65536
      $done = 0
      $last = 0.0
      $sw = [Diagnostics.Stopwatch]::StartNew()
      try {
        while (($n = $inS.Read($buf, 0, $buf.Length)) -gt 0) {
          $outS.Write($buf, 0, $n)
          $done = $done + $n
          $now = $sw.Elapsed.TotalSeconds
          if ($anim -and ($now - $last) -ge 0.1) { Bar $done $total $now $false; $last = $now }
        }
      } finally {
        $outS.Close()
        $inS.Close()
        $rsp.Close()
      }
      $script:dlSecs = $sw.Elapsed.TotalSeconds
      if ($anim) { Bar $done $total $script:dlSecs $true }
      return $done
    }
    catch {
      $msg = $_.Exception.Message
      if ($null -ne $outS) { try { $outS.Close() } catch {} }
      if ($null -ne $inS) { try { $inS.Close() } catch {} }
      if ($null -ne $rsp) { try { $rsp.Close() } catch {} }
      try { if (Test-Path $dest) { Remove-Item -LiteralPath $dest -Force } } catch {}
      if ($attempt -ge 3) { throw ('download failed after 3 tries: ' + $msg) }
      if ($anim) { W ($CR + "  download issue: " + $msg + " " * 8) "DarkYellow" }
    }
  }
}

$art = @(
  '  +---------------------------------------------+',
  '  |   _    ___   __  _   _ _____ _     _____   |',
  '  |  /_\  |_ _| |  \| | | |_   _| |   | ____|  |',
  '  | / _ \  | |  | |\  | | | | | | |   |  _|    |',
  '  |/ ___ \ | |  | | \  | | | | | |___| |___   |',
  '  |/_/   \_\___| |_|  \_|_|_|_|_|_____|_____|  |',
  '  +---------------------------------------------+'
)

Write-Host ""
foreach ($line in $art) {
  if ($anim) { Write-Host $line -ForegroundColor DarkCyan; Start-Sleep -Milliseconds 35 }
  else { Write-Host $line }
}
Write-Host ("    one-command setup  " + $zipUrl) -ForegroundColor DarkGray
Write-Host ""
$tmp = Join-Path $env:TEMP ('ai-butler-boot-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$code = 1
$root = $null
try {
  $pkg = Join-Path $tmp 'pkg.zip'
  Step '[1/3]' 'downloading package ...'
  $script:dlSecs = 0.0
  $size = DownloadPackage $zipUrl $pkg
  Done ('package ready: ' + [math]::Round($size / 1KB, 1) + ' KB in ' + [math]::Round($script:dlSecs, 1) + 's')

  Step '[2/3]' 'extracting ...'
  Spin 'unpacking' '|/-' 500
  Expand-Archive -LiteralPath $pkg -DestinationPath $tmp -Force
  $root = Get-ChildItem -LiteralPath $tmp -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName 'install.ps1') } |
    Select-Object -First 1
  if (-not $root) { throw 'install.ps1 not found inside the package' }
  $inst = Join-Path $root.FullName 'install.ps1'
  Done ('unpacked to ' + $root.Name)

  Write-Host ""
  Write-Host '  ' -NoNewline -ForegroundColor DarkGray
  Write-Host ('-' * 62) -ForegroundColor DarkGray
  Step '[3/3]' 'running installer ...'
  Spin 'starting install.ps1' '|/-' 350
  $argList = @()
  if ($env:AI_BUTLER_INSTALL_ARGS) {
    $argList = @($env:AI_BUTLER_INSTALL_ARGS -split '\s+' | Where-Object { $_ })
  }
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $inst @argList
  $code = $LASTEXITCODE
  if ($null -eq $code) { $code = 0 }

  if ($code -eq 0) {
    Write-Host ''
    Write-Host '  +--------------------------------------------+' -ForegroundColor Green
    Write-Host '  |            setup complete  :)               |' -ForegroundColor Green
    Write-Host '  +--------------------------------------------+' -ForegroundColor Green
  } else {
    Write-Host ''
    Write-Host ('  installer exited with code ' + $code) -ForegroundColor Red
  }
  if ($root) {
    Write-Host ('  uninstall  : powershell -NoProfile -ExecutionPolicy Bypass -File "' + $root.FullName + '\uninstall.ps1"') -ForegroundColor DarkGray
  }
  Write-Host ('  reconfigure: python "' + $env:LOCALAPPDATA + '\ai-butler\tools\wizard.py"') -ForegroundColor DarkGray
}
catch {
  Write-Host ''
  Write-Host ('  FAILED: ' + $_.Exception.Message) -ForegroundColor Red
  $code = 1
}
finally {
  if ($env:AI_BUTLER_KEEP_TEMP -ne '1') {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  } else {
    Write-Host ('  kept temp package: ' + $tmp) -ForegroundColor DarkGray
  }
  try { [Console]::CursorVisible = $true } catch {}
}
# exit code for -File / direct runs; under irm | iex an exit would close the host,
# so detect dot-sourced execution and only publish LASTEXITCODE there.
$dotSourced = $false
try {
  $inv = $MyInvocation.InvocationName
  if ($inv -eq '.' -or $inv -eq '' -or $null -eq $inv) { $dotSourced = $true }
} catch {}
if (-not $dotSourced) { exit $code }
$global:LASTEXITCODE = $code
