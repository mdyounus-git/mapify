param([Parameter(Mandatory)][string]$Cmd)
# Starts a long job detached from this session (WMI, so it survives the
# session ending) and with its console window hidden (SW_HIDE).
$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow = [uint16]0}
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine = $Cmd; CurrentDirectory = "E:\mapify"; ProcessStartupInformation = $si}
"pid $($r.ProcessId) rc $($r.ReturnValue)"
