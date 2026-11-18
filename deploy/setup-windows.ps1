# ─────────────────────────────────────────────────────────────
#  跑課 Garmin-IC8：一鍵架站（Windows 10 / 11）
#  做的事：裝 Python + Tailscale → 設定開機自動啟動 App → 開 Tailscale Funnel 公開網址
#  可以重複執行。
#
#  用法：在專案資料夾按右鍵「在終端機中開啟」，然後執行：
#      powershell -ExecutionPolicy Bypass -File .\deploy\setup-windows.ps1
#  （會自動要求系統管理員權限）
# ─────────────────────────────────────────────────────────────
$ErrorActionPreference = "Continue"   # 外部指令的 stderr 不要直接中斷，改用下面的明確檢查
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
$Port = 8000

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Start-Process powershell -Verb RunAs -ArgumentList "-NoExit -ExecutionPolicy Bypass -File `"$PSCommandPath`""
  exit
}

function Say($m)  { Write-Host "`n▶ $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  ✔ $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "`n✘ $m" -ForegroundColor Red; Read-Host "按 Enter 結束"; exit 1 }
function Refresh-Path { $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }

# ── 1. Python ──
Say "Python 3.12+"
$hasPy = $false
try { py -3 -c "import sys; assert sys.version_info >= (3, 12)" 2>$null; $hasPy = ($LASTEXITCODE -eq 0) } catch {}
if (-not $hasPy) {
  winget install -e --id Python.Python.3.12 --scope machine --accept-package-agreements --accept-source-agreements
  Refresh-Path
}
py -3 --version; Ok "Python 就緒"

# ── 2. 設定檔 .env ──
Say "設定檔 .env"
if (-not (Test-Path .env)) { Copy-Item .env.example .env -ErrorAction Stop; Ok "已建立 .env" }
$cur = ((Get-Content .env -Encoding UTF8) | Where-Object { $_ -like "INVITE_CODE=*" }) -replace '^INVITE_CODE=', ''
if (-not $cur -or $cur -eq "換成你自己的邀請碼") {
  $code = "run-" + -join ((97..122) + (48..57) | Get-Random -Count 6 | ForEach-Object { [char]$_ })
  (Get-Content .env -Encoding UTF8) -replace '^INVITE_CODE=.*', "INVITE_CODE=$code" | Set-Content .env -Encoding UTF8
  Ok "朋友註冊用的邀請碼：$code"
} else { Ok "保留原本的邀請碼" }

# ── 3. 安裝套件（第一次比較久） ──
Say "安裝 App 需要的套件"
if (-not (Test-Path .venv)) { py -3 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
Ok "套件安裝完成"

# ── 4. 開機自動啟動（不用登入 Windows 也會跑） ──
Say "設定開機自動啟動"
$TaskName = "Garmin-IC8 跑課"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Root\run.ps1`" -Log" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -ErrorAction Stop | Out-Null
Start-ScheduledTask -TaskName $TaskName
$up = $false
for ($i = 0; $i -lt 60; $i++) {
  try { Invoke-WebRequest "http://127.0.0.1:$Port/api/meta" -UseBasicParsing -TimeoutSec 2 | Out-Null; $up = $true; break } catch { Start-Sleep 2 }
}
if (-not $up) { Die "App 沒有起來，請看 $Root\data\server.log" }
Ok "App 在 http://127.0.0.1:$Port 跑起來了，重開機會自動啟動"

# ── 5. 不要讓電腦睡著 ──
Say "電源設定"
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null
Ok "插電時不會進入睡眠（筆電闔上蓋子的行為請到「電源選項」另外設定）"

# ── 6. Tailscale ──
Say "Tailscale"
$ts = "C:\Program Files\Tailscale\tailscale.exe"
if (-not (Test-Path $ts)) {
  winget install -e --id Tailscale.Tailscale --accept-package-agreements --accept-source-agreements
  Start-Sleep 5
}
& $ts status *> $null
if ($LASTEXITCODE -ne 0) {
  Warn "接下來會開瀏覽器，請登入 Tailscale（Google / GitHub / Microsoft 帳號都可以）"
  & $ts up
}
Ok "Tailscale 已登入"

# ── 7. Funnel ──
Say "開啟 Tailscale Funnel"
Warn "如果下面出現「Funnel is not enabled」和一個網址：用瀏覽器打開、按同意，指令會自己繼續"
& $ts funnel --bg $Port
$fstat = (& $ts funnel status 2>&1 | Out-String)
if ($fstat -notmatch "https://") { Write-Host $fstat; Die "Funnel 沒有開成功。開通後再執行一次這個腳本即可。" }
Ok "Funnel 已開啟（重開機會自動恢復）"

$dns = ((& $ts status --json | Out-String | ConvertFrom-Json).Self.DNSName).TrimEnd(".")
$url = "https://$dns"
$envText = Get-Content .env -Encoding UTF8
if (-not ($envText -match "^PUBLIC_BASE_URL=$([regex]::Escape($url))$")) {
  $envText -replace '^PUBLIC_BASE_URL=.*', "PUBLIC_BASE_URL=$url" | Set-Content .env -Encoding UTF8
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*uvicorn app.main:app*--port $Port*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep 2
  Start-ScheduledTask -TaskName $TaskName
  for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-WebRequest "http://127.0.0.1:$Port/api/meta" -UseBasicParsing -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 2 }
  }
  Ok "PUBLIC_BASE_URL 設為 $url，App 已重新載入"
}

Start-Sleep 5
try { Invoke-WebRequest "$url/api/meta" -UseBasicParsing -TimeoutSec 15 | Out-Null; Ok "從外部網址測試連線成功" }
catch { Warn "外部網址暫時連不到。剛開通時需要 1–10 分鐘，稍後用手機試試。" }

$invite = ((Get-Content .env -Encoding UTF8) | Where-Object { $_ -like "INVITE_CODE=*" }) -replace '^INVITE_CODE=', ''
Write-Host @"

────────────────────────────────────────────
  🎉 完成！

  網址：   $url
  邀請碼： $invite

  iPhone：用 Safari 打開網址 → 分享 → 加入主畫面
  朋友：   把網址和邀請碼傳給他們就好，不用裝任何東西

  看紀錄：  $Root\data\server.log
  升級套件：.\.venv\Scripts\python.exe -m pip install -U garminconnect，再重開機或重新執行本腳本
  關閉公開："$ts" funnel --bg $Port off
────────────────────────────────────────────
"@ -ForegroundColor Green
Read-Host "按 Enter 結束"
