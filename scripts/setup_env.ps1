# Mapify — create the Python environment.
#
# Run once from the project root:
#     powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
#
# Everything installed here is openly licensed and free to use. Nothing needs
# an account, an API key or a payment. See ATTRIBUTION.md.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Find-Python {
    # PyTorch, EasyOCR and their wheels are most reliable on 3.11-3.13, so
    # prefer 3.12 and fall back to whatever the launcher offers.
    foreach ($v in '3.12', '3.13', '3.11') {
        $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() }
    }
    $p = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($p) { return $p }
    throw "No Python found. Install Python 3.12 from python.org and re-run."
}

$py = Find-Python
Write-Host "Using $py" -ForegroundColor Cyan
& $py -c "import sys; assert sys.version_info[:2] >= (3,11), 'Python 3.11+ required'; print(sys.version)"

if (-not (Test-Path '.venv')) {
    Write-Host "Creating .venv" -ForegroundColor Cyan
    & $py -m venv .venv
}
$venv = Join-Path $root '.venv\Scripts\python.exe'

Write-Host "Upgrading pip" -ForegroundColor Cyan
& $venv -m pip install --upgrade pip setuptools wheel --quiet

Write-Host "Installing CPU PyTorch (large: ~250 MB)" -ForegroundColor Cyan
& $venv -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

Write-Host "Installing application dependencies" -ForegroundColor Cyan
& $venv -m pip install -r requirements.txt

Write-Host ""
Write-Host "Fetching open geodata and the vendored front-end libraries" -ForegroundColor Cyan
& $venv scripts\fetch_data.py

Write-Host "Fetching NASA Earth textures (optional, ~3 MB)" -ForegroundColor Cyan
& $venv scripts\fetch_textures.py --size 4k

Write-Host "Building the country attribute table" -ForegroundColor Cyan
& $venv scripts\build_country_data.py

Write-Host ""
Write-Host "Done. Start the application with:" -ForegroundColor Green
Write-Host "    .\Mapify.bat" -ForegroundColor Green
Write-Host ""
Write-Host "The first analysis downloads ~1.7 GB of model weights from" -ForegroundColor Yellow
Write-Host "Hugging Face. After that the app runs entirely offline." -ForegroundColor Yellow
