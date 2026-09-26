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

$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\Mapify'
$DataDir    = Join-Path $env:LOCALAPPDATA 'Mapify'
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
    if (-not $KeepData) { Write-Note "data      $DataDir" }
    Write-Host ''
    $answer = Read-Host '  Remove Mapify? [y/N]'
    if ($answer -notmatch '^[Yy]') { Write-Host '  Cancelled.'; Write-Host ''; exit 0 }
    Write-Host ''
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

# --------------------------------------------------------------- user state

if (Test-Path -LiteralPath $DataDir) {
    if ($KeepData) {
        Write-Kept "$DataDir  (caches and settings)"
    } else {
        $size = (Get-ChildItem -LiteralPath $DataDir -Recurse -File -ErrorAction SilentlyContinue |
                 Measure-Object -Property Length -Sum).Sum
        Remove-Item -LiteralPath $DataDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Gone ("$DataDir  ({0} MB of caches)" -f [math]::Round($size / 1MB))
    }
}

# ------------------------------------------------------------ model weights

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
