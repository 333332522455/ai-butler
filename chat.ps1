<#
  chat.ps1 - a tiny, dependency-free chat client (like a mini codex) for Windows.

  Usage (no install):
    irm https://333332522455.github.io/ai-butler/chat.ps1 | iex

  Or download once and run as a file:
    powershell -NoProfile -ExecutionPolicy Bypass -File chat.ps1
    powershell -File chat.ps1 -Once "explain this error: 0xC0000005"

  Secrets: the API key is stored ONLY on this machine at
    %APPDATA%\minichat\key.txt   (or env MINICHAT_API_KEY)
  This script never prints your key and never writes it into the repo.
#>

$ErrorActionPreference = "Stop"

# ---------- tiny arg parse (no param() so that irm|iex works too) ----------
$ArgList = @()
if ($args -ne $null) { $ArgList = @($args) }
if ($env:MINICHAT_ARGS) { $ArgList += $env:MINICHAT_ARGS.Split(" ") }

$Once = $false
$OnceText = ""
$ShowHelp = $false
$Model = "deepseek-chat"
$Base = "https://api.deepseek.com/v1"
$KeyOverride = ""

for ($i = 0; $i -lt $ArgList.Count; $i++) {
  $a = [string]$ArgList[$i]
  $opt = $a.Trim()
  switch -Regex ($opt) {
    "^-{1,2}(once)$" {
      $Once = $true
      if ($i + 1 -lt $ArgList.Count) { $OnceText = [string]$ArgList[$i + 1]; $i++ }
      break
    }
    "^-{1,2}(help|h)$" { $ShowHelp = $true; break }
    "^-{1,2}(model|m)$" { if ($i + 1 -lt $ArgList.Count) { $Model = [string]$ArgList[$i + 1]; $i++ }; break }
    "^-{1,2}(base|b)$" { if ($i + 1 -lt $ArgList.Count) { $Base = [string]$ArgList[$i + 1]; $i++ }; break }
    "^-{1,2}(key|k)$" { if ($i + 1 -lt $ArgList.Count) { $KeyOverride = [string]$ArgList[$i + 1]; $i++ }; break }
  }
}
if ($env:MINICHAT_BASE) { $Base = $env:MINICHAT_BASE }
if ($env:MINICHAT_MODEL) { $Model = $env:MINICHAT_MODEL }
$Base = $Base.TrimEnd("/")

# ---------- console encoding (so Chinese replies show up correctly) ----------
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$script:UseColor = $true
try { if ([Console]::IsOutputRedirected) { $script:UseColor = $false } } catch { }

function Say($text, $color) {
  if ($script:UseColor -and $color) { Write-Host $text -ForegroundColor $color }
  else { Write-Host $text }
}
function SayInline($text) { Write-Host $text -NoNewline }

function Show-Help {
  Say "mini chat - commands:" Cyan
  Say "  /file <path>   attach a file to the next message (text only, capped)"
  Say "  /model [name]  show or switch model (default deepseek-chat)"
  Say "  /clear         forget the conversation so far"
  Say "  /save [path]   save this conversation as markdown"
  Say "  /key           re-enter API key (stored at %APPDATA%\minichat\key.txt)"
  Say "  /cost          show tokens used in this session"
  Say "  /help          this text"
  Say "  /exit          quit (Ctrl+C also works)"
  Say "" 
  Say "Non-interactive: powershell -File chat.ps1 -Once "your question"" Gray
}

function Get-KeyPath {
  $dir = Join-Path $env:APPDATA "minichat"
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  return (Join-Path $dir "key.txt")
}

function Get-Key {
  if ($KeyOverride -ne "") { return $KeyOverride.Trim() }
  if ($env:MINICHAT_API_KEY) { return $env:MINICHAT_API_KEY.Trim() }
  $p = Get-KeyPath
  if (Test-Path $p) {
    $k = (Get-Content -LiteralPath $p -Raw -ErrorAction SilentlyContinue)
    if ($k) { return $k.Trim() }
  }
  return ""
}

function Save-Key($k) {
  $p = Get-KeyPath
  [IO.File]::WriteAllText($p, $k.Trim(), (New-Object Text.UTF8Encoding($false)))
  Say ("key saved to " + $p) DarkGray
}

function Mask-Key($k) {
  if (-not $k) { return "(none)" }
  if ($k.Length -le 8) { return "****" }
  return ($k.Substring(0, 4) + "..." + $k.Substring($k.Length - 4))
}

function Measure-Text($t) { if ($t -eq $null) { return 0 }; return ([string]$t).Length }

# ---------- history ----------
$script:History = New-Object System.Collections.ArrayList
$script:TotalIn = 0
$script:TotalOut = 0

function Get-Messages($pendingUser) {
  $msgs = New-Object System.Collections.ArrayList
  [void]$msgs.Add(@{ role = "system"; content = "You are a concise, practical programming assistant. Answer in the same language the user writes in. Prefer short, correct answers with code when useful. If the user asks to read a file, the file content will be provided above their question." })
  foreach ($m in $script:History) { [void]$msgs.Add($m) }
  if ($pendingUser -ne $null) { [void]$msgs.Add(@{ role = "user"; content = $pendingUser }) }
  # keep the request bounded so a long chat never explodes the context
  while ($msgs.Count -gt 41) { $msgs.RemoveAt(1) }
  return $msgs
}

# ---------- the actual request ----------
function Invoke-Chat($userText, $quiet) {
  $key = Get-Key
  if ($key -eq "") {
    Say "No API key yet." Yellow
    Say "Get one at https://platform.deepseek.com/api_keys then paste it here." Gray
    $key = ""
    $ans = Read-Host "API key"
    if ($ans -ne $null) { $key = ([string]$ans).Trim() }
    if ($key -eq "") { Say "Aborted: no key." Red; return $null }
    Save-Key $key
  }

  $body = @{
    model = $Model
    messages = (Get-Messages $userText)
    stream = $true
    stream_options = @{ include_usage = $true }
  } | ConvertTo-Json -Depth 8 -Compress

  $uri = $Base + "/chat/completions"
  $bytes = [Text.Encoding]::UTF8.GetBytes($body)
  $attempt = 0
  $resp = $null
  while ($attempt -lt 2) {
    $attempt++
    $req = [Net.HttpWebRequest]::Create($uri)
    $req.Method = "POST"
    $req.ContentType = "application/json"
    $req.Headers.Add("Authorization", ("Bearer " + $key))
    $req.Accept = "text/event-stream"
    $req.Timeout = 30000
    $req.ReadWriteTimeout = 180000
    $req.ContentLength = $bytes.Length
    try {
      $rs = $req.GetRequestStream()
      $rs.Write($bytes, 0, $bytes.Length)
      $rs.Close()
      $resp = $req.GetResponse()
      break
    } catch [Net.WebException] {
      $we = $_.Exception
      $code = 0
      if ($we.Response -ne $null) {
        try { $code = [int]$we.Response.StatusCode } catch { }
        try {
          $sr = New-Object IO.StreamReader($we.Response.GetResponseStream(), [Text.Encoding]::UTF8)
          $errText = $sr.ReadToEnd(); $sr.Close()
        } catch { $errText = "" }
      } else { $errText = "" }
      if ($code -eq 401) { Say "401 invalid API key. Use /key to set a new one." Red; return $null }
      if ($code -eq 402) { Say "402 insufficient balance on that key." Red; return $null }
      if ($code -eq 429 -and $attempt -lt 2) { Say "429 rate limited, retrying in 2s..." Yellow; Start-Sleep -Seconds 2; continue }
      Say ("request failed: " + $we.Message) Red
      if ($errText) { Say ($errText.Substring(0, [Math]::Min(400, $errText.Length))) DarkGray }
      return $null
    }
  }
  if ($resp -eq $null) { return $null }

  $stream = $resp.GetResponseStream()
  $reader = New-Object IO.StreamReader($stream, [Text.Encoding]::UTF8)
  $sb = New-Object Text.StringBuilder
  $usage = $null
  $printed = $false
  try {
    while (($line = $reader.ReadLine()) -ne $null) {
      if ($line -eq "") { continue }
      if (-not $line.StartsWith("data:")) { continue }
      $payload = $line.Substring(5).Trim()
      if ($payload -eq "[DONE]") { break }
      $obj = $null
      try { $obj = $payload | ConvertFrom-Json } catch { continue }
      if ($obj.usage -ne $null) { $usage = $obj.usage }
      if ($obj.choices -eq $null -or $obj.choices.Count -eq 0) { continue }
      $delta = $obj.choices[0].delta
      if ($delta -eq $null -or $delta.content -eq $null) { continue }
      $piece = [string]$delta.content
      [void]$sb.Append($piece)
      if (-not $quiet) { SayInline $piece; try { [Console]::Out.Flush() } catch { } }
    }
  } finally {
    $reader.Close()
    $resp.Close()
  }
  if (-not $quiet) { Write-Host "" }
  $text = $sb.ToString()
  if ($text -eq "" -and -not $quiet) { Say "(empty reply - try again or /model another name)" DarkGray }

  [void]$script:History.Add(@{ role = "user"; content = $userText })
  [void]$script:History.Add(@{ role = "assistant"; content = $text })
  if ($usage -ne $null) {
    $script:TotalIn += [int]$usage.prompt_tokens
    $script:TotalOut += [int]$usage.completion_tokens
    if (-not $quiet) { Say ("  [tokens in " + $usage.prompt_tokens + " / out " + $usage.completion_tokens + "]") DarkGray }
  }
  return $text
}

# ---------- commands ----------
function Do-File($path) {
  if (-not (Test-Path -LiteralPath $path)) { Say ("no such file: " + $path) Red; return $null }
  $item = Get-Item -LiteralPath $path
  if ($item.PSIsContainer) { Say "that is a folder, not a file" Red; return $null }
  if ($item.Length -gt 2MB) { Say ("file too big (" + [Math]::Round($item.Length / 1MB, 1) + " MB); 2 MB max") Red; return $null }
  $text = "" 
  try { $text = [IO.File]::ReadAllText($item.FullName, [Text.Encoding]::UTF8) } catch { $text = [IO.File]::ReadAllText($item.FullName) }
  $cap = 120000
  $trunc = $false
  if ($text.Length -gt $cap) { $text = $text.Substring(0, $cap); $trunc = $true }
  Say ("attached " + $item.Name + " (" + $text.Length + " chars" + $(if ($trunc) { ", truncated" } else { "" }) + ")") DarkGray
  $fence = ([string][char]96) * 3
  return ("Here is the file " + $item.Name + ": " + [Environment]::NewLine + $fence + [Environment]::NewLine + $text + [Environment]::NewLine + $fence)
}

if ($ShowHelp) { Show-Help; return }
if ($Once) {
  $null = Invoke-Chat $OnceText $false
  return
}

$keyNow = Get-Key
Say "" 
Say "  mini chat  -  zero-install chat client" Cyan
Say ("  model " + $Model + "   base " + $Base + "   key " + (Mask-Key $keyNow)) DarkGray
Say "  /help for commands, /exit to quit" DarkGray
Say "" 

$pending = $null
while ($true) {
  $promptText = "you> "
  if ($pending -ne $null) { $promptText = "you(+file)> " }
  SayInline $promptText
  $line = Read-Host
  if ($line -eq $null) { break }
  $line = $line.Trim()
  if ($line -eq "") { continue }
  if ($line.StartsWith("/")) {
    $parts = $line.Split(" ", 2)
    $cmd = $parts[0].ToLower()
    $rest = "" 
    if ($parts.Count -gt 1) { $rest = $parts[1].Trim() }
    switch ($cmd) {
      "/exit" { return }
      "/quit" { return }
      "/help" { Show-Help }
      "/clear" { $script:History.Clear(); Say "conversation cleared" DarkGray }
      "/cost" { Say ("session tokens: in " + $script:TotalIn + " / out " + $script:TotalOut) DarkGray }
      "/model" {
        if ($rest -eq "") { Say ("model = " + $Model) DarkGray }
        else { $Model = $rest; Say ("model -> " + $Model) DarkGray }
      }
      "/key" {
        $k = (Read-Host "new API key (blank keeps current)").Trim()
        if ($k -ne "") { Save-Key $k; Say ("key -> " + (Mask-Key $k)) DarkGray }
      }
      "/file" {
        if ($rest -eq "") { Say "usage: /file <path>" Yellow }
        else { $pending = Do-File $rest }
      }
      "/save" {
        $target = $rest
        if ($target -eq "") { $target = ("chat-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".md") }
        $out = New-Object Text.StringBuilder
        [void]$out.AppendLine("# chat transcript")
        [void]$out.AppendLine("")
        foreach ($m in $script:History) {
          [void]$out.AppendLine("## " + $m.role)
          [void]$out.AppendLine("")
          [void]$out.AppendLine([string]$m.content)
          [void]$out.AppendLine("")
        }
        $full = [IO.Path]::GetFullPath((Join-Path (Get-Location) $target))
        [IO.File]::WriteAllText($full, $out.ToString(), (New-Object Text.UTF8Encoding($false)))
        Say ("saved " + $full) DarkGray
      }
      default { Say ("unknown command " + $cmd + " - /help lists them") Yellow }
    }
    continue
  }
  $send = $line
  if ($pending -ne $null) { $send = $pending + [Environment]::NewLine + [Environment]::NewLine + $line; $pending = $null }
  SayInline "ai> "
  $null = Invoke-Chat $send $false
}
