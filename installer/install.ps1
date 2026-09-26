<#
.SYNOPSIS
    Install Mapify for the current user.

.DESCRIPTION
    A per-user install, on purpose. It needs no administrator, touches no
    machine-wide registry hive, and writes nothing outside the current
    user's profile -- so it can be undone completely, and nothing it does
    can affect another account on this computer.

        Program        %LOCALAPPDATA%\Programs\Mapify
        Data & caches  %LOCALAPPDATA%\Mapify
        Shortcuts      Start menu, and the desktop if asked
        Registry       HKCU:\...\Uninstall\Mapify   (the Apps list entry)

    That is the complete list. uninstall.ps1 removes all four.

.PARAMETER Source
    The built application folder -- the one containing Mapify.exe. Defaults
    to ..\dist\Mapify relative to this script.

.PARAMETER Desktop
    Also create a desktop shortcut.

.PARAMETER Force
    Replace an existing installation without asking.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installer\install.ps1 -Desktop

.NOTES
    Mapify is not code-signed. Windows SmartScreen will warn the first time
    the executable runs, and that warning is correct: it means the publisher
    is unverified, which is true. Do not disable SmartScreen to silence it.
    Verify what you are running instead -- the source is at
    https://github.com/mdyounus-git/mapify and this script only copies files
    you already have on disk.
#>

[CmdletBinding()]
param(
    [string] $Source,
    [string] $Root,
    [switch] $Desktop,
    [switch] $Force
)

$ErrorActionPreference = 'Stop'

$AppName     = 'Mapify'
$Publisher   = 'Mapify (open source)'
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

if (-not $Source) { $Source = Join-Path $ProjectRoot 'dist\Mapify' }

# Everything Mapify owns under one root, on the project's own drive: the
# program, its caches, its settings, its log, and the 1.6 GB of weights it
# downloads. An installation that scatters a gigabyte across C: and hides
# the rest in a profile directory is one nobody can find, move or delete.
if (-not $Root) { $Root = Join-Path $ProjectRoot 'app' }

$InstallDir  = $Root
$DataDir     = Join-Path $Root 'data'
$ModelsDir   = Join-Path $Root 'models'
$StartMenu   = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$UninstKey   = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Mapify'

function Write-Step  { param($m) Write-Host "  $m" -ForegroundColor Cyan }
function Write-Ok    { param($m) Write-Host "  $m" -ForegroundColor Green }
function Write-Note  { param($m) Write-Host "  $m" -ForegroundColor DarkGray }
function Write-Warn2 { param($m) Write-Host "  $m" -ForegroundColor Yellow }

Write-Host ''
Write-Host '  Mapify - install' -ForegroundColor White
Write-Host '  ----------------' -ForegroundColor DarkGray
Write-Host ''

# ---------------------------------------------------------------- preflight

$Exe = Join-Path $Source 'Mapify.exe'
if (-not (Test-Path -LiteralPath $Exe)) {
    Write-Host "  Mapify.exe is not in:" -ForegroundColor Red
    Write-Host "      $Source" -ForegroundColor Red
    Write-Host ''
    Write-Host '  Build it first:' -ForegroundColor Yellow
    Write-Host '      .venv\Scripts\python build_exe.py' -ForegroundColor Yellow
    Write-Host ''
    exit 1
}

# WebView2 draws the window. Without it the application starts and then
# fails with a COM error, so say so here instead, while there is still
# somebody watching the console.
$wv2 = $null
foreach ($path in @(
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
)) {
    try {
        $v = (Get-ItemProperty -Path $path -Name pv -ErrorAction Stop).pv
        if ($v -and $v -ne '0.0.0.0') { $wv2 = $v; break }
    } catch { }
}
if ($wv2) {
    Write-Note "WebView2 runtime $wv2"
} else {
    Write-Warn2 'The Microsoft Edge WebView2 Runtime was not found.'
    Write-Warn2 'Mapify uses it to draw its window and will not start without it.'
    Write-Warn2 'Get it (free, from Microsoft) at:'
    Write-Warn2 '    https://developer.microsoft.com/microsoft-edge/webview2/'
    Write-Host ''
    Write-Note 'Installing anyway - it can be added afterwards.'
}

$SourceSize = (Get-ChildItem -LiteralPath $Source -Recurse -File |
               Measure-Object -Property Length -Sum).Sum
$SourceMb   = [math]::Round($SourceSize / 1MB)

$Version = 'unknown'
$initPy = Join-Path $ProjectRoot 'mapify\__init__.py'
if (Test-Path -LiteralPath $initPy) {
    $m = Select-String -Path $initPy -Pattern '__version__\s*=\s*"([^"]+)"'
    if ($m) { $Version = $m.Matches[0].Groups[1].Value }
}

Write-Note "version       $Version"
Write-Note "from          $Source"
Write-Note "size          $SourceMb MB"
Write-Note "install to    $InstallDir"
Write-Note "data in       $DataDir"
Write-Note "models in     $ModelsDir"
Write-Host ''

# The 1.6 GB of model weights are not ours and are not in this build. Say
# where they will go before an install implies otherwise.
$hfCache = if ($env:HF_HUB_CACHE) { $env:HF_HUB_CACHE }
           elseif ($env:HF_HOME)  { Join-Path $env:HF_HOME 'hub' }
           else { Join-Path $env:USERPROFILE '.cache\huggingface\hub' }
$clipDir    = Join-Path $hfCache 'models--openai--clip-vit-large-patch14'
$modelsHub  = Join-Path $ModelsDir 'hub'
$targetClip = Join-Path $modelsHub 'models--openai--clip-vit-large-patch14'

if (Test-Path -LiteralPath $targetClip) {
    Write-Note 'model weights already in place'
} elseif (Test-Path -LiteralPath $clipDir) {
    Write-Note 'model weights found in the shared cache and will be copied,'
    Write-Note 'so this installation does not depend on anything outside it'
} else {
    Write-Note 'first run will download about 1.6 GB of model weights into'
    Write-Note "  $ModelsDir"
}
Write-Host ''

# ------------------------------------------------------------------ replace

if (Test-Path -LiteralPath $InstallDir) {
    if (-not $Force) {
        $answer = Read-Host "  $InstallDir already exists. Replace it? [y/N]"
        if ($answer -notmatch '^[Yy]') { Write-Host '  Cancelled.'; exit 0 }
    }
    # A running copy holds its own files open; removing them would half
    # uninstall the application and leave nothing that starts.
    $running = Get-Process -Name 'Mapify' -ErrorAction SilentlyContinue
    if ($running) {
        Write-Host '  Mapify is running. Close it and run this again.' -ForegroundColor Red
        exit 1
    }
    Write-Step 'Removing the previous installation'
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
}

# --------------------------------------------------------------------- copy

Write-Step "Copying $SourceMb MB"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Path (Join-Path $Source '*') -Destination $InstallDir -Recurse -Force
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# The uninstaller has to survive the folder it removes, so it lives beside
# the program rather than in it being the thing that deletes itself.
Copy-Item -Path (Join-Path $ScriptDir 'uninstall.ps1') `
          -Destination (Join-Path $InstallDir 'uninstall.ps1') -Force

foreach ($doc in @('LICENSE', 'ATTRIBUTION.md', 'README.md')) {
    $src = Join-Path $ProjectRoot $doc
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination $InstallDir -Force
    }
}

# The encoded GPS gallery is a 98 MB file that takes five to eight minutes of
# CPU to produce, and it is keyed by a hash of the gallery it came from -- the
# loader checks that key and rejects a mismatch. So if this checkout has one
# already, copying it is safe and saves the new installation that entire wait.
$GallerySource = Join-Path $ProjectRoot 'data\cache'
$GalleryTarget = Join-Path $DataDir 'cache'
$gallery = @(Get-ChildItem -LiteralPath $GallerySource -Filter 'geoclip_gallery_*.npy' `
                           -ErrorAction SilentlyContinue)
if ($gallery.Count -gt 0) {
    Write-Step 'Copying the encoded GPS gallery (saves 5-8 minutes on first run)'
    New-Item -ItemType Directory -Force -Path $GalleryTarget | Out-Null
    foreach ($g in $gallery) {
        Copy-Item -LiteralPath $g.FullName -Destination $GalleryTarget -Force
    }
} else {
    Write-Note 'no encoded gallery to copy - the first run will spend'
    Write-Note '  five to eight minutes building it, once'
}

# The weights are the biggest thing Mapify is responsible for, and the
# installed application now looks for them beside itself. Copied rather
# than moved: the Hugging Face cache is shared and other tools may be
# using the same files.
if (-not (Test-Path -LiteralPath $targetClip) -and
    (Test-Path -LiteralPath $clipDir)) {
    $clipMb = [math]::Round((Get-ChildItem -LiteralPath $clipDir -Recurse -File |
                             Measure-Object -Property Length -Sum).Sum / 1MB)
    Write-Step "Copying $clipMb MB of model weights (saves re-downloading them)"
    New-Item -ItemType Directory -Force -Path $modelsHub | Out-Null
    Copy-Item -LiteralPath $clipDir -Destination $modelsHub -Recurse -Force
}

# EasyOCR keeps its detection and recognition weights in ~/.EasyOCR by
# default -- another 565 MB on the system drive. Same treatment.
$easySource = Join-Path $env:USERPROFILE '.EasyOCR'
$easyTarget = Join-Path $ModelsDir 'easyocr'
if ((Test-Path -LiteralPath $easySource) -and
    -not (Test-Path -LiteralPath (Join-Path $easyTarget 'model'))) {
    $easyMb = [math]::Round((Get-ChildItem -LiteralPath $easySource -Recurse -File `
                             -ErrorAction SilentlyContinue |
                             Measure-Object -Property Length -Sum).Sum / 1MB)
    Write-Step "Copying $easyMb MB of OCR models"
    New-Item -ItemType Directory -Force -Path $easyTarget | Out-Null
    Copy-Item -Path (Join-Path $easySource '*') -Destination $easyTarget `
              -Recurse -Force
}

$InstalledExe = Join-Path $InstallDir 'Mapify.exe'
$InstalledSize = (Get-ChildItem -LiteralPath $InstallDir -Recurse -File |
                  Measure-Object -Property Length -Sum).Sum

# ---------------------------------------------------------------- shortcuts

function New-Shortcut {
    param($Path, $Target, $WorkDir, $Description)
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath       = $Target
    $link.WorkingDirectory = $WorkDir
    $link.Description      = $Description
    $link.IconLocation     = "$Target,0"
    $link.Save()
    [Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null
}

Write-Step 'Creating shortcuts'
New-Shortcut -Path (Join-Path $StartMenu 'Mapify.lnk') `
             -Target $InstalledExe -WorkDir $InstallDir `
             -Description 'Image geolocation reconnaissance'

if ($Desktop) {
    New-Shortcut -Path (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mapify.lnk') `
                 -Target $InstalledExe -WorkDir $InstallDir `
                 -Description 'Image geolocation reconnaissance'
}

# ----------------------------------------------------------------- registry

# One key, under the current user. This is what puts Mapify in Settings ->
# Apps so it can be uninstalled the way every other application is, which is
# where a person will look for it.
Write-Step 'Registering in Apps & features'
New-Item -Path $UninstKey -Force | Out-Null
$uninstallCommand =
    'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' +
    (Join-Path $InstallDir 'uninstall.ps1') + '"'

$values = @{
    DisplayName     = $AppName
    DisplayVersion  = $Version
    Publisher       = $Publisher
    DisplayIcon     = $InstalledExe
    InstallLocation = $InstallDir
    UninstallString = $uninstallCommand
    NoModify        = 1
    NoRepair        = 1
    EstimatedSize   = [int]($InstalledSize / 1KB)
    URLInfoAbout    = 'https://github.com/mdyounus-git/mapify'
    InstallDate     = (Get-Date -Format 'yyyyMMdd')
}
foreach ($name in $values.Keys) {
    $type = if ($values[$name] -is [int]) { 'DWord' } else { 'String' }
    New-ItemProperty -Path $UninstKey -Name $name -Value $values[$name] `
                     -PropertyType $type -Force | Out-Null
}

# --------------------------------------------------------------------- done

Write-Host ''
Write-Ok 'Installed.'
Write-Host ''
Write-Note "program     $InstallDir"
Write-Note "data        $DataDir"
Write-Note "models      $ModelsDir"
Write-Note 'start menu  Mapify'
if ($Desktop) { Write-Note 'desktop     Mapify' }
Write-Note 'uninstall   Settings > Apps > Mapify'
Write-Host ''
Write-Warn2 'Mapify is not code-signed, so SmartScreen may warn the first time'
Write-Warn2 'it runs. That warning is accurate - the publisher is unverified.'
Write-Warn2 'Choose More info > Run anyway if you trust what you just built.'
Write-Host ''
