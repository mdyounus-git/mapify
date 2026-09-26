<#
.SYNOPSIS
    Remove Mapify.

.DESCRIPTION
    Undoes exactly what install.ps1 did, and nothing else:

        %LOCALAPPDATA%\Programs\Mapify        the program
        Start menu and desktop shortcuts
        HKCU:\...\Uninstall\Mapify            the Apps list entry
        %LOCALAPPDATA%\Mapify                 data and caches, if asked

    Two things are deliberately left alone unless you ask for them:

    * The downloaded model weights, about 1.6 GB in the Hugging Face cache
      under your profile. They are not Mapify's -- other tools use the same
      cache -- and re-downloading them is a long wait for anyone who
      reinstalls. Use -PurgeModels to remove them anyway.
    * Any reports you exported. Those are yours and are wherever you saved
      them.

.PARAMETER KeepData
    Leave %LOCALAPPDATA%\Mapify in place: the lookup cache, the encoded GPS
    gallery, and config.toml if you wrote one. Reinstalling then skips the
    minute of gallery encoding.

.PARAMETER PurgeModels
    Also delete the downloaded CLIP weights from the Hugging Face cache.
    Read the warning it prints first: the cache is shared.

.PARAMETER Quiet
    Do not ask anything. Used when Windows invokes this from Apps & features
    is not the default -- even there it asks, because deleting a gigabyte of
    somebody's disk without a question is not acceptable.
#>

[CmdletBinding()]
param(
    [switch] $KeepData,
    [switch] $PurgeModels,
    [switch] $Quiet
)

$ErrorActionPreference = 'Stop'

# Where Mapify actually went, rather than where it used to go by default.
# An installation can sit on any drive, so the location is read from the
# registry entry the installer wrote; failing that, from the fact that this
# script is copied into the installation it removes.
$UninstKeyPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Mapify'
$InstallDir = $null
try {
    $InstallDir = (Get-ItemProperty -Path $UninstKeyPath -Name InstallLocation `
                                    -ErrorAction Stop).InstallLocation
} catch { }
if (-not $InstallDir -or -not (Test-Path -LiteralPath $InstallDir)) {
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    if (Test-Path -LiteralPath (Join-Path $here 'Mapify.exe')) {
        $InstallDir = $here
    } else {
        $InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\Mapify'
    }
}

# Data and models live inside the installation now. An older install kept
# them under the user profile, so that location is cleaned up too.
$DataDir    = Join-Path $InstallDir 'data'
$ModelsDir  = Join-Path $InstallDir 'models'
$LegacyData = Join-Path $env:LOCALAPPDATA 'Mapify'
$StartMenu  = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Mapify.lnk'
$DesktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mapify.lnk'
$UninstKey  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Mapify'

function Write-Step { param($m) Write-Host "  $m" -ForegroundColor Cyan }
function Write-Note { param($m) Write-Host "  $m" -ForegroundColor DarkGray }
function Write-Gone { param($m) Write-Host "  removed  $m" -ForegroundColor Green }
function Write-Kept { param($m) Write-Host "  kept     $m" -ForegroundColor DarkGray }

Write-Host ''
Write-Host '  Mapify - uninstall' -ForegroundColor White
Write-Host '  ------------------' -ForegroundColor DarkGray
Write-Host ''

$running = Get-Process -Name 'Mapify' -ErrorAction SilentlyContinue
if ($running) {
    Write-Host '  Mapify is running. Close it first.' -ForegroundColor Red
    Write-Host ''
    exit 1
}

if (-not $Quiet) {
    Write-Note "program   $InstallDir"
    if (-not $KeepData) {
        Write-Note "data      $DataDir"
        if (Test-Path -LiteralPath $ModelsDir) {
            $mb = [math]::Round((Get-ChildItem -LiteralPath $ModelsDir -Recurse -File `
                                 -ErrorAction SilentlyContinue |
                                 Measure-Object -Property Length -Sum).Sum / 1MB)
            Write-Note "models    $ModelsDir  ($mb MB)"
        }
    }
    Write-Host ''
    $answer = Read-Host '  Remove Mapify? [y/N]'
    if ($answer -notmatch '^[Yy]') { Write-Host '  Cancelled.'; Write-Host ''; exit 0 }
    Write-Host ''
}

# ----------------------------------------------- rescue anything being kept
#
# Data and models live inside the installation, so they have to be moved
# out before the installation goes. Doing it the other way round would
# make -KeepData a lie.

$KeptRoot = $null
if ($KeepData) {
    $KeptRoot = Join-Path (Split-Path -Parent $InstallDir) 'mapify-kept'
    New-Item -ItemType Directory -Force -Path $KeptRoot | Out-Null
    foreach ($dir in @($DataDir, $ModelsDir)) {
        if (Test-Path -LiteralPath $dir) {
            $dest = Join-Path $KeptRoot (Split-Path -Leaf $dir)
            if (Test-Path -LiteralPath $dest) {
                Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction SilentlyContinue
            }
            Move-Item -LiteralPath $dir -Destination $dest -Force
            Write-Kept $dest
        }
    }
}

# -------------------------------------------------------------- the program

if (Test-Path -LiteralPath $InstallDir) {
    Write-Step 'Removing the program'
    # This script lives inside the folder it is deleting. PowerShell has the
    # file open, so the directory cannot go while it runs; copy out, and let
    # the copy finish the job after this process exits.
    $self = $MyInvocation.MyCommand.Path
    $inPlace = $self -and $self.StartsWith($InstallDir, 'OrdinalIgnoreCase')

    if ($inPlace) {
        $stager = Join-Path $env:TEMP ('mapify-uninstall-' + [guid]::NewGuid().ToString('N') + '.ps1')
        @"
Start-Sleep -Seconds 2
Remove-Item -LiteralPath '$InstallDir' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath '`$PSCommandPath' -Force -ErrorAction SilentlyContinue
"@ | Set-Content -LiteralPath $stager -Encoding UTF8
        Start-Process -FilePath 'powershell.exe' `
            -ArgumentList '-NoProfile', '-WindowStyle', 'Hidden',
                          '-ExecutionPolicy', 'Bypass', '-File', $stager
        Write-Gone "$InstallDir  (completes in a moment)"
    } else {
        Remove-Item -LiteralPath $InstallDir -Recurse -Force
        Write-Gone $InstallDir
    }
} else {
    Write-Note "not installed at $InstallDir"
}

# ---------------------------------------------------------------- shortcuts

foreach ($lnk in @($StartMenu, $DesktopLnk)) {
    if (Test-Path -LiteralPath $lnk) {
        Remove-Item -LiteralPath $lnk -Force
        Write-Gone $lnk
    }
}

# ----------------------------------------------------------------- registry

if (Test-Path -LiteralPath $UninstKey) {
    Remove-Item -LiteralPath $UninstKey -Recurse -Force
    Write-Gone 'Apps & features entry'
}

# --------------------------------------------------- an older install's data
#
# Mapify used to keep its state under the user profile. Anyone upgrading
# from that layout has a stale copy there, and leaving it behind would be
# exactly the kind of litter an uninstaller exists to avoid.

if (Test-Path -LiteralPath $LegacyData) {
    if ($KeepData) {
        Write-Kept "$LegacyData  (from an earlier install)"
    } else {
        Remove-Item -LiteralPath $LegacyData -Recurse -Force `
                    -ErrorAction SilentlyContinue
        Write-Gone "$LegacyData  (from an earlier install)"
    }
}

# ------------------------------------------------------------ model weights
#
# Mapify's own copy lived inside the installation and has already gone with
# it (or been moved aside by -KeepData). What is left to consider is the
# *shared* Hugging Face cache, which belongs to whatever else uses it.

$hfCache = if ($env:HF_HUB_CACHE) { $env:HF_HUB_CACHE }
           elseif ($env:HF_HOME)  { Join-Path $env:HF_HOME 'hub' }
           else { Join-Path $env:USERPROFILE '.cache\huggingface\hub' }
$clipDir = Join-Path $hfCache 'models--openai--clip-vit-large-patch14'

if (Test-Path -LiteralPath $clipDir) {
    if ($PurgeModels) {
        $size = (Get-ChildItem -LiteralPath $clipDir -Recurse -File -ErrorAction SilentlyContinue |
                 Measure-Object -Property Length -Sum).Sum
        Write-Host ''
        Write-Host '  The Hugging Face cache is shared between tools.' -ForegroundColor Yellow
        Write-Host '  Removing these weights affects anything else that uses them.' -ForegroundColor Yellow
        if (-not $Quiet) {
            $answer = Read-Host ('  Delete {0} MB of CLIP weights? [y/N]' -f [math]::Round($size / 1MB))
            if ($answer -notmatch '^[Yy]') {
                Write-Kept $clipDir
                $PurgeModels = $false
            }
        }
        if ($PurgeModels) {
            Remove-Item -LiteralPath $clipDir -Recurse -Force -ErrorAction SilentlyContinue
            Write-Gone ("$clipDir  ({0} MB)" -f [math]::Round($size / 1MB))
        }
    } else {
        Write-Kept "$clipDir  (model weights; -PurgeModels removes them)"
    }
}

Write-Host ''
Write-Host '  Done.' -ForegroundColor Green
Write-Host ''
