; Mapify - Inno Setup script
;
; Produces a single Setup.exe for people who should not have to run a
; PowerShell script. install.ps1 remains the one that is actually tested on
; every build; this wraps the same decisions in the installer UI Windows
; users expect.
;
; Build it with Inno Setup 6 (free, open source, https://jrsoftware.org/):
;
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\mapify.iss
;
; Output lands in installer\output\Mapify-Setup-<version>.exe
;
; The same three rules as the PowerShell installer:
;
;   * per-user. PrivilegesRequired=lowest, so no UAC prompt, no machine-wide
;     registry, and nothing that touches another account on the computer.
;   * the model weights are not in here. They are 1.6 GB, they are not ours,
;     and the application asks before fetching them on first run.
;   * unsigned, and it says so. Do not pretend otherwise.

#define AppName        "Mapify"
#define AppPublisher   "Mapify (open source)"
#define AppURL         "https://github.com/mdyounus-git/mapify"
#define AppExe         "Mapify.exe"
#ifndef AppVersion
  #define AppVersion   "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir    "..\dist\Mapify"
#endif

[Setup]
AppId={{7C5E1A44-0B2E-4A2F-9E4B-5A1C7D3F8E90}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases

; Per-user, always. A tool that analyses somebody's photographs has no
; business installing itself for every account on the machine.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes

LicenseFile=..\LICENSE
InfoBeforeFile=first-run-notice.txt
OutputDir=output
OutputBaseFilename=Mapify-Setup-{#AppVersion}
SetupIconFile=..\assets\mapify.ico
UninstallDisplayIcon={app}\{#AppExe}
WizardStyle=modern

; The payload is ~1.1 GB of PyTorch. LZMA2 at max takes a while to pack and
; roughly halves it, which is worth it exactly once at build time.
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
    GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\ATTRIBUTION.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";      DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";   Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\Attribution and licences"; Filename: "{app}\ATTRIBUTION.md"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
    WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Caches only -- all of it reconstructible, none of it the user's own work.
; Exported reports live wherever the user saved them and are never touched.
Type: filesandordirs; Name: "{localappdata}\{#AppName}\cache"

[Code]
const
  WV2_HKLM32 = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WV2_HKLM   = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WV2_HKCU   = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result := False;
  if RegQueryStringValue(HKLM, WV2_HKLM32, 'pv', Version)
     or RegQueryStringValue(HKLM, WV2_HKLM, 'pv', Version)
     or RegQueryStringValue(HKCU, WV2_HKCU, 'pv', Version) then
    Result := (Version <> '') and (Version <> '0.0.0.0');
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not WebView2Installed() then
  begin
    // A warning, not a refusal. The runtime can be installed afterwards and
    // Mapify will then start; blocking the install would be worse than
    // explaining the one thing that is missing.
    MsgBox('Mapify draws its window with the Microsoft Edge WebView2 Runtime, '
           + 'and it was not found on this computer.'#13#10#13#10
           + 'Mapify will not start until it is installed. It is free, from '
           + 'Microsoft, at:'#13#10
           + 'https://developer.microsoft.com/microsoft-edge/webview2/'#13#10#13#10
           + 'Setup will continue - you can install the runtime afterwards.',
           mbInformation, MB_OK);
  end;
end;
