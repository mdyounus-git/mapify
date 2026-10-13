param([Parameter(Mandatory)][string]$Cmd)
# Starts a long job (an evaluation, a build, the street-photo memory) detached
# from the calling session and with its console window hidden:
#
#   powershell -File scripts\launch_hidden.ps1 -Cmd 'cmd.exe /c bench\run-x.cmd'
#
# Through WMI, so the job survives the session that started it ending (jobs
# started with Start-Process or from a tool's background shell died with it);
# hidden (SW_HIDE) so no terminal window pops up on the desktop. WMI refuses
# CREATE_NO_WINDOW, so the window is created hidden instead. Runs in the
# repository root.
$root = Split-Path -Parent $PSScriptRoot
$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow = [uint16]0}
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine = $Cmd; CurrentDirectory = $root; ProcessStartupInformation = $si}
"pid $($r.ProcessId) rc $($r.ReturnValue)"
