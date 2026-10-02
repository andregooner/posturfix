; ==============================================================================
; Inno Setup Script for PosturFix
; 100% Offline AI Posture Tracker & Ergonomics Assistant
; ==============================================================================

#define MyAppName "PosturFix"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "PosturFix"
#define MyAppURL "https://github.com/andregooner/posturfix"
#define MyAppExeName "PosturFix.exe"

[Setup]
; Unique AppId identifier (do not change across versions for seamless in-place updates)
AppId={{8B91FA37-4512-4BD1-9556-9937D50C484E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; 1. Installation Target Directory (Defaults to Program Files via {autopf})
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; Administrative installation privileges for Program Files
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; Output configuration
OutputDir=.
OutputBaseFilename=PosturFix_Setup_v{#MyAppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

; Compression settings (Ultra LZMA2 for high compression ratio)
Compression=lzma2/ultra64
SolidCompression=yes

; Modern Windows UI styling
WizardStyle=modern
WizardSizePercent=100

; Target 64-bit architecture
ArchitecturesInstallIn64BitMode=x64compatible
DisableDirPage=no
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; 2. Shortcut Tasks
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkable
; 3. Autostart Task
Name: "autostart"; Description: "Start {#MyAppName} automatically when Windows starts (runs minimized in tray)"; GroupDescription: "Startup Options:"; Flags: checkable

[Files]
; Include the PyInstaller output from dist\PosturFix
Source: "dist\PosturFix\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\PosturFix\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion

[Icons]
; Start Menu Application Shortcut
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
; Desktop Shortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; Start Menu Uninstaller Shortcut
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

[Registry]
; 4. Autostart on Windows Boot (HKCU\Software\Microsoft\Windows\CurrentVersion\Run)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"" --minimized"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
; Post-installation launch option
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up any runtime logs or temporary files upon uninstallation
Type: files; Name: "{app}\*.log"
Type: files; Name: "{app}\*.tmp"

[Code]
// Gracefully terminate any running instances before installing or upgrading
function InitializeSetup(): Boolean;
var
  ErrorCode: Integer;
begin
  Result := True;
  ShellExec('open', 'taskkill.exe', '/f /im PosturFix.exe', '', SW_HIDE, ewWaitUntilTerminated, ErrorCode);
end;

// Gracefully terminate running instances before uninstalling
function InitializeUninstall(): Boolean;
var
  ErrorCode: Integer;
begin
  Result := True;
  ShellExec('open', 'taskkill.exe', '/f /im PosturFix.exe', '', SW_HIDE, ewWaitUntilTerminated, ErrorCode);
end;
