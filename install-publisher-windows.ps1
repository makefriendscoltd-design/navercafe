param(
  [string]$Python = "C:\Users\likim\AppData\Local\Programs\Python\Python312\python.exe"
)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") { throw "This installer must run on Windows." }
if (-not (Test-Path $Python)) { throw "Python 3.12 not found: $Python" }

$version = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $version.Trim() -ne "3.12") {
  throw "Cafe publisher requires Python 3.12; got $version"
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $root
try {
  & $Python -m pip install -r requirements.txt
  if ($LASTEXITCODE -ne 0) { throw "Cafe publisher dependency installation failed." }

  & $Python -m unittest discover -s tests -v
  if ($LASTEXITCODE -ne 0) { throw "Cafe publisher unit tests failed." }

  & $Python -c "import importlib.metadata as m; import notebook_cafe_auto; assert m.version('notebooklm-py') == '0.7.3'; print('Cafe publisher import probe: OK')"
  if ($LASTEXITCODE -ne 0) { throw "Cafe publisher import probe failed." }

  if (-not (Test-Path (Join-Path $root "config.ini"))) {
    Copy-Item (Join-Path $root "config.example.ini") (Join-Path $root "config.ini")
    Write-Output "Created local config.ini from the example. Fill its local-only credentials before running."
  }
} finally {
  Pop-Location
}

Write-Output "Cafe publisher install checks complete."
Write-Output "Next: & `"$Python`" -m notebooklm login"
Write-Output "Then: & `"$Python`" -m notebooklm auth check --test --json"
