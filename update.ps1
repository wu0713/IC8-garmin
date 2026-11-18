# 更新到 GitHub 上最新的版本並重開服務（Windows PowerShell）
# 用法：在 garmin-ic8 資料夾執行  .\update.ps1
# data 資料夾（帳號、Garmin 連結、紀錄）和 .env 不會被動到。
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$TaskName = "Garmin-IC8 跑課"
$Port = 8000

function Ok($m)   { Write-Host "  ✔ $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "  ✘ $m" -ForegroundColor Red; exit 1 }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Die "找不到 git。先安裝：winget install --id Git.Git -e" }
}

Write-Host "1/4 檢查有沒有新版…"
$ErrorActionPreference = "Continue"
git fetch origin main 2>&1 | Out-Null
$ErrorActionPreference = "Stop"
$changes = git log --oneline HEAD..origin/main
if (-not $changes) { Ok "已經是最新版，不用更新"; exit 0 }
Write-Host "   要更新的內容："
$changes | ForEach-Object { Write-Host "     $_" }

# 本機有改過程式（不含 data、.env 這些忽略的檔案）就先停，避免蓋掉
$dirty = git status --porcelain --untracked-files=no
if ($dirty) {
  Write-Host $dirty
  Die "這台伺服器上有改過的程式檔（上面列的）。先截圖給 Claude，確認後再更新。"
}

Write-Host "2/4 下載新版…"
$ErrorActionPreference = "Continue"
git pull --ff-only origin main 2>&1 | ForEach-Object { Write-Host "     $_" }
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -ne 0) { Die "下載失敗（git pull）" }
Ok "程式已更新到 $(git log -1 --format='%h %s')"

Write-Host "3/4 安裝需要的套件…"
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Die "找不到 .venv，請先執行 deploy\setup-windows.ps1" }
& $py -m pip install -q --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { Die "套件安裝失敗" }
Ok "套件已安裝"

Write-Host "4/4 重開服務…"
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*uvicorn app.main:app*--port $Port*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 2
Start-ScheduledTask -TaskName $TaskName
$up = $false
for ($i = 0; $i -lt 40; $i++) {
  try { Invoke-WebRequest "http://127.0.0.1:$Port/api/meta" -UseBasicParsing -TimeoutSec 2 | Out-Null; $up = $true; break } catch { Start-Sleep 2 }
}
if (-not $up) { Die "服務沒有起來，請看 data\server.log 最後幾行" }
Ok "更新完成，服務已重新啟動。手機上的 App 重新整理就會看到新版。"
