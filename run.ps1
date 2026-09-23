$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3.12 -m venv .venv
    } else {
        $installRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
        $installed = @(Get-ChildItem -Path $installRoot -Filter python.exe -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending)
        $python = if ($installed.Count) { $installed[0].FullName } else { 'python' }
        & $python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Install Python 3.12.' }
}

& $venvPython -c "import streamlit, pytest; assert streamlit.__version__ == '1.55.0' and pytest.__version__ == '8.4.2'" *> $null
if ($LASTEXITCODE -ne 0) {
    & $venvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Could not install dependencies.' }
}
& $venvPython -m streamlit run app.py --server.address 127.0.0.1 @args
