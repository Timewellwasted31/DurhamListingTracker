#define MyAppName "Durham Listing Tracker"
#define MyAppVersion "2.13.0"
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

[Files]
; Only the program: the exe and _internal (which carries the hidden browser too).
; The rest of the dist folder is this computer's own use of the app and must never be packed:
; data (listings, config with the phone key, profiles), browser (site sign-ins), cache, logs.
; The app creates those folders itself on first run.
Source: "W:\ListingTracker\app\dist\DurhamListingTracker\DurhamListingTracker.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "W:\ListingTracker\app\dist\DurhamListingTracker\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent unchecked

[UninstallDelete]
; Clean up cache (disposable), but leave data/ alone so the user keeps their database
Type: filesandordirs; Name: "{app}\cache"
Type: filesandordirs; Name: "{app}\logs"
