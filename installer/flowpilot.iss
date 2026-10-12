; FlowPilot Windows installer (Inno Setup 6).
; Built by .github/workflows/release.yml:  ISCC /DAppVersion=0.2.0 installer\flowpilot.iss
; Installs per user (no admin rights needed), so the in-app updater can run it silently.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8F3C6E52-7A41-4C7B-9E0D-5B1A2F4C9D37}
AppName=FlowPilot
AppVersion={#AppVersion}
AppVerName=FlowPilot {#AppVersion}
AppPublisher=FlowPilot
AppPublisherURL=https://github.com/shoukattaz-ux/Sadakatpro
AppSupportURL=https://github.com/shoukattaz-ux/Sadakatpro/issues
AppUpdatesURL=https://github.com/shoukattaz-ux/Sadakatpro/releases
VersionInfoVersion={#AppVersion}
VersionInfoProductName=FlowPilot
VersionInfoDescription=FlowPilot - no-code browser automation
DefaultDirName={localappdata}\Programs\FlowPilot
DefaultGroupName=FlowPilot
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist\installer
OutputBaseFilename=FlowPilot-Setup
SetupIconFile=..\flowpilot\resources\icons\flowpilot.ico
UninstallDisplayIcon={app}\FlowPilot.exe
UninstallDisplayName=FlowPilot
WizardStyle=modern
WizardImageFile=wizard-large.bmp
WizardSmallImageFile=wizard-small.bmp
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; remove the previous version's program files first (user data in %APPDATA%\FlowPilot is untouched)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\FlowPilot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\FlowPilot"; Filename: "{app}\FlowPilot.exe"
Name: "{autodesktop}\FlowPilot"; Filename: "{app}\FlowPilot.exe"; Tasks: desktopicon

[Run]
; normal install: offer to start FlowPilot on the last page
Filename: "{app}\FlowPilot.exe"; Description: "{cm:LaunchProgram,FlowPilot}"; Flags: nowait postinstall skipifsilent
; silent install = in-app update: start FlowPilot again automatically
Filename: "{app}\FlowPilot.exe"; Flags: nowait; Check: WizardSilent
