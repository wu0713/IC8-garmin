# 不用 Docker 的啟動方式（Windows PowerShell）
# 手動執行：powershell -ExecutionPolicy Bypass -File .\run.ps1
# 加上 -Log 會把輸出寫到 data\server.log（開機自動啟動時用）
param([switch]$Log)
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) {
  py -3.12 -m venv .venv
}
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $py -m pip install -q --disable-pip-version-check -r requirements.txt

if (Test-Path .env) {
  Get-Content .env -Encoding UTF8 | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim())
  }
}
New-Item -ItemType Directory -Force data | Out-Null
$env:PYTHONIOENCODING = "utf-8"
$uv = "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1"
if ($Log) {
  # 用相對路徑避免 cmd 的引號問題（資料夾路徑有空白也沒關係）；用 cmd 重新導向：PowerShell 5.1 直接導向 stderr 會把正常的 log 當成錯誤而中斷
  cmd /c ".venv\Scripts\python.exe $uv >> data\server.log 2>&1"
} else {
  cmd /c ".venv\Scripts\python.exe $uv"
}
