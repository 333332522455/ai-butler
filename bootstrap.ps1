# AI Butler - one command bootstrap (ASCII only on purpose, safe under irm | iex)
#
#   irm https://333332522455.github.io/ai-butler/bootstrap.ps1 | iex
#
# Env overrides:
#   AI_BUTLER_ZIP_URL       alternative package zip (default: GitHub archive of main)
#   AI_BUTLER_INSTALL_ARGS  extra args for install.ps1, e.g. -DryRun or -NoAutostart
#   AI_BUTLER_KEEP_TEMP     set to 1 to keep the extracted package

$ErrorActionPreference = 'Stop'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$repo = '333332522455/ai-butler'
if ($env:AI_BUTLER_ZIP_URL) { $zipUrl = $env:AI_BUTLER_ZIP_URL }
else { $zipUrl = 'https://codeload.github.com/' + $repo + '/zip/refs/heads/main' }

Write-Host ''
Write-Host 'AI Butler bootstrap' -ForegroundColor Cyan
Write-Host ('  package : ' + $zipUrl)

$tmp = Join-Path $env:TEMP ('ai-butler-boot-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$code = 1
$root = $null
try {
  Write-Host '[1/3] downloading ...' -ForegroundColor Cyan
  $pkg = Join-Path $tmp 'pkg.zip'
  Invoke-WebRequest -Uri $zipUrl -OutFile $pkg -UseBasicParsing

  Write-Host '[2/3] extracting ...' -ForegroundColor Cyan
  Expand-Archive -LiteralPath $pkg -DestinationPath $tmp -Force
  $root = Get-ChildItem -LiteralPath $tmp -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName 'install.ps1') } |
    Select-Object -First 1
  if (-not $root) { throw 'install.ps1 not found inside the package' }
  $inst = Join-Path $root.FullName 'install.ps1'

  Write-Host '[3/3] running installer ...' -ForegroundColor Cyan
  $argList = @()
  if ($env:AI_BUTLER_INSTALL_ARGS) {
    $argList = @($env:AI_BUTLER_INSTALL_ARGS -split '\s+' | Where-Object { $_ })
  }
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $inst @argList
  $code = $LASTEXITCODE
  if ($null -eq $code) { $code = 0 }

  if ($code -eq 0) {
    Write-Host ''
    Write-Host 'DONE - AI Butler installed.' -ForegroundColor Green
  } else {
    Write-Host ''
    Write-Host ('installer exited with code ' + $code) -ForegroundColor Red
  }
  if ($root) {
    Write-Host ('  uninstall  : powershell -NoProfile -ExecutionPolicy Bypass -File "' + $root.FullName + '\uninstall.ps1"')
  }
  Write-Host ('  reconfigure: python "' + $env:LOCALAPPDATA + '\ai-butler\tools\wizard.py"')
}
catch {
  Write-Host ''
  Write-Host ('FAILED: ' + $_.Exception.Message) -ForegroundColor Red
  $code = 1
}
finally {
  if ($env:AI_BUTLER_KEEP_TEMP -ne '1') {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  } else {
    Write-Host ('  kept temp package: ' + $tmp)
  }
}
$global:LASTEXITCODE = $code
