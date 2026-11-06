; DropLens installer — builds with Inno Setup 6 (https://jrsoftware.org/isinfo.php)
; Produces a professional Setup .exe with big branded artwork, silent install,
; desktop + Start-menu shortcuts, uninstaller and AppUserModelId for taskbar grouping.
;
; Build:      iscc "installer\DropLens.iss"            (per-user, no admin)
; Per-machine: iscc "installer\DropLens.iss" /DMyPrivileges=admin
; (build_installer.ps1 passes the version in from dropLens/__init__.py)

#ifndef MyAppVersion
  #define MyAppVersion "2.3.0"
#endif
#ifndef MyAppOutputDir
  #define MyAppOutputDir "dist"
#endif
#ifndef MyRepoRoot
  #define MyRepoRoot ".."
#endif
; "lowest" = per-user install into %LOCALAPPDATA%\Programs (no elevation).
; "admin"  = per-machine install into {autopf}; use it for shared workstations.
#ifndef MyPrivileges
  #define MyPrivileges "lowest"
#endif

#define MyAppName "DropLens"
#define MyAppPublisher "AIONEXT"
#define MyAppExeName "DropLens.exe"
#define MyAppUrl "https://github.com/AIONEXT/DropLens-2.0"
; Must match SetCurrentProcessExplicitAppUserModelID() in dropLens/main.py
#define MyAppUserModelID "AIONEXT.DropLens.Desktop"

[Setup]
; A stable AppId (never derive it from the version) is what makes an upgrade
; replace the previous install instead of sitting beside it.
AppId={{7F3E1B6A-9C24-4A6E-B8D5-0D2C9A3F1E05}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppUrl}
AppSupportURL={#MyAppUrl}/issues
AppUpdatesURL={#MyAppUrl}/releases
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired={#MyPrivileges}
PrivilegesRequiredOverridesAllowed=dialog commandline
; MIT license, shown on the wizard's license page
LicenseFile={#MyRepoRoot}\LICENSE
OutputDir={#MyRepoRoot}\{#MyAppOutputDir}
OutputBaseFilename=Setup-DropLens-{#MyAppVersion}
SetupIconFile={#MyRepoRoot}\build\appicon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; big branded welcome image (744x386) — generated at build time if present; else default
WizardImageFile={#MyRepoRoot}\build\installer_welcome.bmp
WizardImageStretch=no
WizardSmallImageFile={#MyRepoRoot}\build\installer_side.bmp
CloseApplications=yes
RestartApplications=no
; Windows 10 1809+ (the app targets 10/11)
MinVersion=10.0.17763

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"
Name: "tray"; Description: "Run &silently in the system tray after install"; GroupDescription: "Startup:"

[Files]
Source: "{#MyRepoRoot}\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyRepoRoot}\build\appicon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyRepoRoot}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyRepoRoot}\docs\USER_GUIDE.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "{#MyRepoRoot}\docs\SECURITY.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "{#MyRepoRoot}\docs\ENTERPRISE_DEPLOYMENT.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\appicon.ico"; AppUserModelID: "{#MyAppUserModelID}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\appicon.ico"; Tasks: desktopicon; AppUserModelID: "{#MyAppUserModelID}"

[Run]
; First launch after install (skipped for silent installs). The --warmup pass
; below pre-imports the payload in the background.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#MyAppExeName}"; Parameters: "--warmup"; Flags: nowait runhidden; StatusMsg: "Preparing DropLens…"
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Tasks: tray

[UninstallDelete]
; The catalogue under %LOCALAPPDATA%\DropLens is user data and is deliberately
; NOT removed here: uninstalling/reinstalling must not force a full re-index.
Type: filesandordirs; Name: "{app}\docs"

[Code]
var
  ErrorCode: Integer;

function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    { Preserve an existing catalogue when switching install scope: the data
      folder is per-user, so a per-machine install must not look empty. }
    DataDir := ExpandConstant('{localappdata}\DropLens');
    if not DirExists(DataDir) then
      ForceDirectories(DataDir);
  end;
end;
