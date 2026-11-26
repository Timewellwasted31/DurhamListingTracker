#define MyAppName "Durham Listing Tracker"
#define MyAppVersion "2.14.1"
#define MyAppExeName "DurhamListingTracker.exe"

[Setup]
AppId={{D8F3A2B1-4E5C-6D7F-8A9B-0C1D2E3F4A5B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Durham Listing Tracker
DefaultDirName={localappdata}\Programs\DurhamListingTracker
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=W:\ListingTracker\installer
OutputBaseFilename=DurhamListingTrackerSetup
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "phonefirewall"; Description: "Let the phone app connect (adds Windows Firewall rules for the home network and this computer's hotspot; Windows asks for administrator permission)"; GroupDescription: "Phone app:"

[Files]
; Only the program: the exe and _internal, plus the firewall setup script.
; The rest of the dist folder is this computer's own use of the app and must never be packed:
; data (listings, config with the phone key, profiles), browser (site sign-ins), cache, logs.
; The app creates those folders itself on first run.
Source: "W:\ListingTracker\app\dist\DurhamListingTracker\DurhamListingTracker.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "W:\ListingTracker\app\dist\DurhamListingTracker\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "W:\ListingTracker\installer\firewall.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\logs"

[Icons]
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Windows Firewall rules for the phone app (see firewall.ps1). The installer itself needs no
; administrator rights; only this step does, so Windows asks for them here. If the person says
; no, everything else is still installed, and installing again offers the step again.
Filename: "{sysnative}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\firewall.ps1"" -Exe ""{app}\{#MyAppExeName}"" -Log ""{app}\logs\firewall-setup.log"""; Verb: runas; Flags: shellexec runhidden waituntilterminated; Tasks: phonefirewall; StatusMsg: "Setting up Windows Firewall for the phone app..."
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent unchecked

[UninstallRun]
Filename: "{sysnative}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\firewall.ps1"" -Exe ""{app}\{#MyAppExeName}"" -Remove"; Verb: runas; Flags: shellexec runhidden waituntilterminated; Tasks: phonefirewall; RunOnceId: "RemovePhoneFirewall"

[UninstallDelete]
; Clean up cache (disposable), but leave data/ alone so the user keeps their database
Type: filesandordirs; Name: "{app}\cache"
Type: filesandordirs; Name: "{app}\logs"
