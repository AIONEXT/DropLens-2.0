; DropLens installer — builds with Inno Setup 6 (https://jrsoftware.org/isinfo.php)
; Produces a professional Setup .exe with big branded artwork, silent install,
; desktop + Start-menu shortcuts, uninstaller and AppUserModelId for taskbar grouping.
;
; Build:  iscc "installer\DropLens.iss"   (or run build_installer.ps1)

#ifndef MyAppVersion
  #define MyAppVersion "2.0.0"
#endif
#ifndef MyAppOutputDir
  #define MyAppOutputDir "dist"
#endif

#define MyAppName "DropLens"
#define MyAppPublisher "DropLens"
#define MyAppExeName "DropLens.exe"
#define MyAppId "D9B3F9C4-8A2E-4F5D-9C1E-{MyAppVersion}"

[Setup]
AppId={{7F3E1B6A-9C24-4A6E-B8D5-0D2C9A3F1E05}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#MyAppOutputDir}
OutputBaseFilename=Setup-DropLens-{#MyAppVersion}
SetupIconFile=build\appicon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; big branded welcome image (744x386) — generated at build time if present; else default
WizardImageFile=build\installer_welcome.bmp
WizardImageStretch=no
WizardSmallImageFile=build\installer_side.bmp

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"
Name: "tray"; Description: "Run &silently in the system tray after install"; GroupDescription: "Startup:"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\appicon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\appicon.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\appicon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Tasks: tray

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Exec(ExpandConstant('{app}\{#MyAppExeName}'), '--warmup', '', SW_HIDE, ewNoWait, ErrorCode);
end;