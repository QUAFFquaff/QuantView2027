$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Project Python environment not found: $PythonPath"
}

Set-Location -LiteralPath $ProjectRoot
& $PythonPath -m quantview ui

