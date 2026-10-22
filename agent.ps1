# agent.ps1 - a small Claude-Code-like agent for Windows PowerShell (no install, no Node).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File agent.ps1
#   powershell -File agent.ps1 -Once "find the bug in main.c" -yes
#
# Design notes:
#   - Workspace is confined: every path must stay inside the workspace root (default: Desktop\ai-butler-workspace).
#   - Read-only tools (list/read/grep) run freely; run_command asks for approval once, then you can answer "a" to allow the rest.
#   - After a task finishes, a small self-test runs so you see proof it still works.
#   - Key: %APPDATA%\minichat\key.txt or MINICHAT_API_KEY or -key. Never written to the repo.

$ErrorActionPreference = "Stop"

# ---------------- args ----------------
$ArgList = @()
if ($args -ne $null) { $ArgList = @($args) }
if ($env:AGENT_ARGS) { $ArgList += $env:AGENT_ARGS.Split(" ") }

$Model = "deepseek-chat"
$Base = "https://api.deepseek.com/v1"
$KeyOverride = ""
$OnceText = ""
$DoOnce = $false
$AutoApprove = $false
$ShowHelp = $false
$SelfTest = $false
$WsArg = ""

for ($i = 0; $i -lt $ArgList.Count; $i++) {
  $a = [string]$ArgList[$i]
  $next = ""
  if ($i + 1 -lt $ArgList.Count) { $next = [string]$ArgList[$i + 1] }
  switch -Regex ($a.Trim()) {
    "^-{1,2}(once)$" { $DoOnce = $true; $OnceText = $next; $i++; break }
    "^-{1,2}(ws|workspace)$" { $WsArg = $next; $i++; break }
    "^-{1,2}(model|m)$" { $Model = $next; $i++; break }
    "^-{1,2}(base|b)$" { $Base = $next; $i++; break }
    "^-{1,2}(key|k)$" { $KeyOverride = $next; $i++; break }
    "^-{1,2}(yes|yolo)$" { $AutoApprove = $true; break }
    "^-{1,2}(selftest)$" { $SelfTest = $true; break }
    "^-{1,2}(help|h)$" { $ShowHelp = $true; break }
  }
}
if ($env:MINICHAT_BASE) { $Base = $env:MINICHAT_BASE }
if ($env:MINICHAT_MODEL) { $Model = $env:MINICHAT_MODEL }
$Base = $Base.TrimEnd("/")

try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$script:UseColor = $true
try { if ([Console]::IsOutputRedirected) { $script:UseColor = $false } } catch { }

function Say($t, $c) { if ($script:UseColor -and $c) { Write-Host $t -ForegroundColor $c } else { Write-Host $t } }
function SayInline($t) { Write-Host $t -NoNewline }

function Show-Help {
  Say "agent.ps1 - a small Claude-Code-like agent" Cyan
  Say ""
  Say "  powershell -File agent.ps1                     start in the workspace" Gray
  Say "  powershell -File agent.ps1 -ws D:\myproj       use another workspace" Gray
  Say "  powershell -File agent.ps1 -Once \"do something\"   one-shot task" Gray
  Say "  powershell -File agent.ps1 -yes                auto-approve run_command" Gray
  Say "  powershell -File agent.ps1 -selftest           check tools without the API" Gray
  Say ""
  Say "Inside: /help  /ws  /tools  /clear  /exit. Prefix a line with ! to run it yourself." Gray
}

# ---------------- workspace ----------------
if ($WsArg -ne "") { $WsPath = $WsArg }
elseif ($env:AGENT_WORKSPACE) { $WsPath = $env:AGENT_WORKSPACE }
else { $WsPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "ai-butler-workspace" }
if (-not (Test-Path -LiteralPath $WsPath)) { $null = New-Item -ItemType Directory -Path $WsPath -Force }
$script:WS = (Resolve-Path -LiteralPath $WsPath).Path.TrimEnd("\")

function Resolve-InWorkspace($p) {
  if ([string]::IsNullOrWhiteSpace($p)) { return $script:WS }
  try {
    if ([IO.Path]::IsPathRooted($p)) { $full = [IO.Path]::GetFullPath($p) }
    else { $full = [IO.Path]::GetFullPath((Join-Path $script:WS $p)) }
  } catch { return $null }
  if ($full.TrimEnd("\") -ieq $script:WS) { return $script:WS }
  if ($full.StartsWith($script:WS + "\", [StringComparison]::OrdinalIgnoreCase)) { return $full }
  return $null
}

function Rel-Path($full) {
  if ($full -ieq $script:WS) { return "." }
  $r = $full.Substring($script:WS.Length).TrimStart("\")
  if ($r -eq "") { return "." }
  return $r
}

# ---------------- tools ----------------
function Tool-ListDir($p) {
  $full = Resolve-InWorkspace $p
  if ($full -eq $null) { return "ERROR: path is outside the workspace: $p" }
  if (-not (Test-Path -LiteralPath $full)) { return "ERROR: no such directory: $p" }
  if (-not (Test-Path -LiteralPath $full -PathType Container)) { return "ERROR: not a directory: $p" }
  $rows = @()
  try {
    $items = Get-ChildItem -LiteralPath $full -Force -ErrorAction Stop | Sort-Object PSIsContainer, Name
  } catch { return ("ERROR: " + $_.Exception.Message) }
  foreach ($it in $items) {
    $name = $it.Name
    if ($it.PSIsContainer) { $rows += ($name + "/") }
    else { $rows += ($name + "  (" + $it.Length + " bytes)") }
  }
  if ($rows.Count -eq 0) { return "(empty directory)" }
  return (($rows -join [Environment]::NewLine))
}

function Tool-ReadFile($p, $offset, $limit) {
  $full = Resolve-InWorkspace $p
  if ($full -eq $null) { return "ERROR: path is outside the workspace: $p" }
  if (-not (Test-Path -LiteralPath $full)) { return "ERROR: no such file: $p" }
  if (Test-Path -LiteralPath $full -PathType Container) { return "ERROR: that is a directory: $p" }
  $fi = Get-Item -LiteralPath $full
  if ($fi.Length -gt 2MB) { return ("ERROR: file too big (" + $fi.Length + " bytes), use grep instead") }
  if ($offset -eq $null -or $offset -lt 1) { $offset = 1 }
  if ($limit -eq $null -or $limit -lt 1) { $limit = 200 }
  if ($limit -gt 1000) { $limit = 1000 }
  $lines = @()
  try { $lines = [IO.File]::ReadAllLines($full) } catch { return ("ERROR: " + $_.Exception.Message) }
  $sb = New-Object Text.StringBuilder
  $end = [Math]::Min($lines.Count, $offset - 1 + $limit)
  for ($n = $offset - 1; $n -lt $end; $n++) {
    [void]$sb.AppendLine(([string]($n + 1)).PadLeft(5) + "| " + $lines[$n])
  }
  if ($end -lt $lines.Count) { [void]$sb.AppendLine("... (" + $lines.Count + " lines total, showing " + $offset + "-" + $end + ")") }
  return $sb.ToString()
}

function Tool-Grep($pattern, $p) {
  $full = Resolve-InWorkspace $p
  if ($full -eq $null) { return "ERROR: path is outside the workspace: $p" }
  if (-not (Test-Path -LiteralPath $full)) { return "ERROR: no such path: $p" }
  if ([string]::IsNullOrWhiteSpace($pattern)) { return "ERROR: empty pattern" }
  $target = $full
  if (Test-Path -LiteralPath $full -PathType Container) { $target = Join-Path $full "*" }
  $hits = @()
  try {
    $found = Select-String -Path $target -Pattern $pattern -SimpleMatch -ErrorAction SilentlyContinue | Select-Object -First 60
  } catch { return ("ERROR: " + $_.Exception.Message) }
  foreach ($h in $found) {
    $text = ([string]$h.Line).Trim()
    if ($text.Length -gt 200) { $text = $text.Substring(0, 200) + "..." }
    $hits += ((Rel-Path $h.Path) + ":" + $h.LineNumber + ": " + $text)
  }
  if ($hits.Count -eq 0) { return "(no matches)" }
  return ($hits -join [Environment]::NewLine)
}

function Tool-RunCommand($cmd, $timeoutSec) {
  if ([string]::IsNullOrWhiteSpace($cmd)) { return "ERROR: empty command" }
  if ($timeoutSec -eq $null -or $timeoutSec -lt 5) { $timeoutSec = 120 }
  if ($timeoutSec -gt 600) { $timeoutSec = 600 }
  $psi = New-Object Diagnostics.ProcessStartInfo
  $psi.FileName = $env:ComSpec
  $psi.Arguments = "/c " + $cmd
  $psi.WorkingDirectory = $script:WS
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
  $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
  try { $proc = [Diagnostics.Process]::Start($psi) } catch { return ("ERROR: cannot start: " + $_.Exception.Message) }
  $so = $proc.StandardOutput.ReadToEndAsync()
  $se = $proc.StandardError.ReadToEndAsync()
  $ms = $timeoutSec * 1000
  $exited = $proc.WaitForExit($ms)
  if (-not $exited) {
    try { $proc.Kill() } catch { }
    return ("ERROR: command timed out after " + $timeoutSec + "s")
  }
  $out = $so.Result
  $err = $se.Result
  $code = $proc.ExitCode
  $cap = 8000
  if ($out.Length -gt $cap) { $out = $out.Substring(0, $cap) + [Environment]::NewLine + "...(output truncated)" }
  if ($err.Length -gt $cap) { $err = $err.Substring(0, $cap) + [Environment]::NewLine + "...(stderr truncated)" }
  $sb = New-Object Text.StringBuilder
  [void]$sb.AppendLine("exit_code: " + $code)
  if ($out.Trim() -ne "") { [void]$sb.AppendLine("stdout:"); [void]$sb.AppendLine($out.TrimEnd()) }
  if ($err.Trim() -ne "") { [void]$sb.AppendLine("stderr:"); [void]$sb.AppendLine($err.TrimEnd()) }
  if ($out.Trim() -eq "" -and $err.Trim() -eq "") { [void]$sb.AppendLine("(no output)") }
  return $sb.ToString()
}

# ---------------- tool schema ----------------
function New-Tool($name, $desc, $props, $required) {
  $schema = @{ type = "object"; properties = $props; required = $required }
  return @{ type = "function"; function = @{ name = $name; description = $desc; parameters = $schema } }
}

$script:Tools = @(
  (New-Tool "list_dir" "List files in a directory inside the workspace." @{ path = @{ type = "string"; description = "relative path, default ." } } @("path")),
  (New-Tool "read_file" "Read a text file inside the workspace. Returns numbered lines." @{ path = @{ type = "string" }; offset = @{ type = "integer"; description = "1-based first line, default 1" }; limit = @{ type = "integer"; description = "max lines, default 200" } } @("path")),
  (New-Tool "grep" "Search text inside the workspace (literal match). Returns path:line: text." @{ pattern = @{ type = "string" }; path = @{ type = "string"; description = "file or directory, default ." } } @("pattern")),
  (New-Tool "run_command" "Run a shell command inside the workspace and return exit code, stdout, stderr." @{ command = @{ type = "string" }; timeout_sec = @{ type = "integer"; description = "default 120, max 600" } } @("command"))
)

$script:SysPrompt = "You are a coding agent running in a Windows workspace. You have tools: list_dir, read_file, grep, run_command. Everything you touch must be inside the workspace root. Work step by step: inspect before you claim, run tests when useful, and keep answers short. If a task is already done, say so instead of inventing work. Never claim you ran something you did not."

# ---------------- api ----------------
function Get-Key {
  if ($KeyOverride -ne "") { return $KeyOverride.Trim() }
  if ($env:MINICHAT_API_KEY) { return $env:MINICHAT_API_KEY.Trim() }
  $p = Join-Path (Join-Path $env:APPDATA "minichat") "key.txt"
  if (Test-Path -LiteralPath $p) { $k = Get-Content -LiteralPath $p -Raw; if ($k) { return $k.Trim() } }
  return ""
}

function Invoke-Api($messages) {
  $body = @{ model = $Model; messages = $messages; tools = $script:Tools; tool_choice = "auto" } | ConvertTo-Json -Depth 12 -Compress
  $bytes = [Text.Encoding]::UTF8.GetBytes($body)
  $req = [Net.HttpWebRequest]::Create($script:Base + "/chat/completions")
  $req.Method = "POST"
  $req.ContentType = "application/json"
  $req.Headers.Add("Authorization", ("Bearer " + $script:Key))
  $req.Timeout = 60000
  $req.ReadWriteTimeout = 300000
  $req.ContentLength = $bytes.Length
  $rs = $req.GetRequestStream()
  $rs.Write($bytes, 0, $bytes.Length)
  $rs.Close()
  $resp = $req.GetResponse()
  $sr = New-Object IO.StreamReader($resp.GetResponseStream(), [Text.Encoding]::UTF8)
  $text = $sr.ReadToEnd()
  $sr.Close()
  $resp.Close()
  return ($text | ConvertFrom-Json)
}

# ---------------- loop ----------------
$script:History = New-Object System.Collections.ArrayList

function Run-Turn($userText) {
  [void]$script:History.Add(@{ role = "user"; content = $userText })
  $iter = 0
  while ($iter -lt 25) {
    $iter++
    $msgs = New-Object System.Collections.ArrayList
    [void]$msgs.Add(@{ role = "system"; content = $script:SysPrompt })
    foreach ($m in $script:History) { [void]$msgs.Add($m) }
    while ($msgs.Count -gt 60) { $msgs.RemoveAt(1) }
    $resp = $null
    try { $resp = Invoke-Api $msgs }
    catch { Say ("request failed: " + $_.Exception.Message) Red; return }
    if ($resp.error -ne $null) { Say ("api error: " + $resp.error.message) Red; return }
    $msg = $resp.choices[0].message
    $calls = @()
    if ($msg.tool_calls -ne $null) { $calls = @($msg.tool_calls) }
    if ($calls.Count -eq 0) {
      $answer = [string]$msg.content
      [void]$script:History.Add(@{ role = "assistant"; content = $answer })
      Say "" 
      Say $answer White
      Say "" 
      if ($resp.usage -ne $null) { Say ("  [tokens in " + $resp.usage.prompt_tokens + " / out " + $resp.usage.completion_tokens + "]") DarkGray }
      return
    }
    if ($msg.content) { Say ([string]$msg.content) DarkGray }
    [void]$script:History.Add(@{ role = "assistant"; content = $msg.content; tool_calls = $calls })
    foreach ($call in $calls) {
      $name = [string]$call.function.name
      $rawArgs = [string]$call.function.arguments
      $result = ""
      $argv = $null
      try { $argv = $rawArgs | ConvertFrom-Json } catch { $argv = $null }
      switch ($name) {
        "list_dir" {
          $p = "." 
          if ($argv -ne $null -and $argv.path) { $p = [string]$argv.path }
          Say ("  [tool] list_dir " + $p) DarkCyan
          $result = Tool-ListDir $p
        }
        "read_file" {
          $p = "" 
          if ($argv -ne $null) { $p = [string]$argv.path }
          $off = 1
          $lim = 200
          if ($argv -ne $null -and $argv.offset) { $off = [int]$argv.offset }
          if ($argv -ne $null -and $argv.limit) { $lim = [int]$argv.limit }
          Say ("  [tool] read_file " + $p + " (" + $off + "+" + $lim + ")") DarkCyan
          $result = Tool-ReadFile $p $off $lim
        }
        "grep" {
          $pat = "" 
          $p = "." 
          if ($argv -ne $null -and $argv.pattern) { $pat = [string]$argv.pattern }
          if ($argv -ne $null -and $argv.path) { $p = [string]$argv.path }
          Say ("  [tool] grep "" + $pat + "" in " + $p) DarkCyan
          $result = Tool-Grep $pat $p
        }
        "run_command" {
          $cmdText = "" 
          $tos = 120
          if ($argv -ne $null -and $argv.command) { $cmdText = [string]$argv.command }
          if ($argv -ne $null -and $argv.timeout_sec) { $tos = [int]$argv.timeout_sec }
          Say "" 
          Say ("  [run] " + $cmdText) Yellow
          $allowed = $true
          if (-not $script:AutoApprove) {
            SayInline "  allow? [y]es / [n]o / [a]ll: "
            $ans = ([string](Read-Host)).Trim().ToLower()
            if ($ans -eq "a") { $script:AutoApprove = $true }
            elseif ($ans -ne "y" -and $ans -ne "yes") { $allowed = $false }
          }
          if ($allowed) { $result = Tool-RunCommand $cmdText $tos }
          else { $result = "USER DENIED: the user refused to run this command." }
        }
        default { $result = ("ERROR: unknown tool " + $name) }
      }
      $preview = ([string]$result) -replace "`r?`n", " "
      if ($preview.Length -gt 240) { $preview = $preview.Substring(0, 240) + " ..." }
      if ($preview.Trim().Length -gt 0) { Say ("  [out] " + $preview) DarkGray }
      [void]$script:History.Add(@{ role = "tool"; tool_call_id = [string]$call.id; content = $result })
    }
  }
  Say "stopped: too many steps (25). Try a smaller task." Yellow
}

# ---------------- self test ----------------
function Run-SelfTest {
  $script:ok = 0
  $script:bad = 0
  $chk = { param($n, $c, $e) if ($c) { $script:ok++; Say ("  PASS  " + $n) Green } else { $script:bad++; Say ("  FAIL  " + $n + " " + $e) Red } }
  $probe = Join-Path $script:WS ("selftest-" + [Guid]::NewGuid().ToString("N").Substring(0, 6))
  $null = New-Item -ItemType Directory -Path $probe -Force
  [IO.File]::WriteAllText((Join-Path $probe "hello.txt"), "alpha`nbeta`ngamma", (New-Object Text.UTF8Encoding($false)))
  $r1 = Tool-ListDir $probe
  & $chk "list_dir sees the probe" ($r1 -match "hello.txt") $r1
  $r2 = Tool-ReadFile (Join-Path $probe "hello.txt") 1 10
  & $chk "read_file numbers lines" ($r2 -match "2| beta") $r2
  $r3 = Tool-Grep "beta" $probe
  & $chk "grep finds text" ($r3 -match "hello.txt:2: beta") $r3
  $r4 = Tool-RunCommand "echo hi-from-run" 30
  & $chk "run_command executes" ($r4 -match "exit_code: 0" -and $r4 -match "hi-from-run") $r4
  $r5 = Tool-ReadFile "C:\Windows\win.ini" 1 1
  & $chk "outside paths are refused" ($r5 -match "outside the workspace") $r5
  $r6 = Tool-ReadFile "..\..\win.ini" 1 1
  & $chk "../ escape is refused" ($r6 -match "outside the workspace") $r6
  Remove-Item -LiteralPath $probe -Recurse -Force -ErrorAction SilentlyContinue
  Say "" 
  Say ("selftest: " + $script:ok + " passed, " + $script:bad + " failed") Cyan
  if ($script:bad -gt 0) { exit 2 }
}

# ---------------- main ----------------
if ($ShowHelp) { Show-Help; exit 0 }
if ($SelfTest) { Run-SelfTest; exit 0 }

$script:Key = Get-Key
if ($script:Key -eq "") {
  Say "No API key yet. Get one at https://platform.deepseek.com/api_keys" Yellow
  $k = ([string](Read-Host "API key")).Trim()
  if ($k -eq "") { Say "aborted" Red; exit 2 }
  $dir = Join-Path $env:APPDATA "minichat"
  if (-not (Test-Path $dir)) { $null = New-Item -ItemType Directory -Path $dir -Force }
  [IO.File]::WriteAllText((Join-Path $dir "key.txt"), $k, (New-Object Text.UTF8Encoding($false)))
  $script:Key = $k
}

if ($DoOnce) {
  Say ("workspace: " + $script:WS) DarkGray
  Run-Turn $OnceText
  $r = Tool-RunCommand "echo selftest-ok" 30
  if ($r -notmatch "selftest-ok") { Say "post-task selftest FAILED" Red; exit 2 }
  Say "post-task selftest: ok" DarkGray
  exit 0
}

Say "" 
Say "  agent  -  workspace-confined coding agent" Cyan
Say ("  workspace " + $script:WS + "   model " + $Model) DarkGray
Say "  read tools run freely; run_command asks once (/help for commands)" DarkGray
Say "" 
while ($true) {
  SayInline "you> "
  $line = Read-Host
  if ($line -eq $null) { break }
  $line = ([string]$line).Trim()
  if ($line -eq "") { continue }
  if ($line -eq "/exit" -or $line -eq "/quit") { break }
  if ($line -eq "/help") { Show-Help; continue }
  if ($line -eq "/ws") { Say $script:WS DarkGray; continue }
  if ($line -eq "/tools") { Say "list_dir, read_file, grep, run_command" DarkGray; continue }
  if ($line -eq "/clear") { $script:History.Clear(); Say "context cleared" DarkGray; continue }
  if ($line.StartsWith("!")) {
    $c = $line.Substring(1).Trim()
    Say ("  [run] " + $c) Yellow
    $out = Tool-RunCommand $c 120
    Say $out DarkGray
    continue
  }
  Run-Turn $line
}
