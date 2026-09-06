; Inno Setup script for File Manager. Needs Inno Setup 6.3 or newer, for
; ArchitecturesAllowed=x64compatible. Compiled by `packaging/build.py`, which
; passes the version in:
;
;     iscc /DAppVersion=0.7.0 packaging\installer.iss
;
; Two decisions are load bearing and both are about updating.
;
; It installs per user, into %LOCALAPPDATA%\Programs. A per-machine install
; into Program Files would put a UAC prompt in front of every update, and an
; update that needs a password is an update that gets postponed until the
; version gap is wide enough to be frightening. This is a single-user tool on
; a single machine; the registry keys and the shortcut belong to that user.
;
; The output filename has no spaces in it. GitHub replaces spaces in an asset
; name with dots as it takes the upload, so a setup exe called
; "File Manager Setup.exe" arrives as "File.Manager.Setup.exe" and the URL in
; latest.json -- written before the upload -- points at nothing. Redline PDF
; shipped that bug and it only appeared on machines running the older build.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "File Manager"
#define AppExe "FileManager.exe"
#define Publisher "Robbuie"
#define RepoUrl "https://github.com/Robbuie/FileManager"

[Setup]
; Never change this GUID. It is how Windows knows an install is an upgrade of
; this application rather than a second copy of it, and how the updater's
; silent run lands on top of what is already there. It is also copied into
; `app/core/updates.py` as APP_ID: the application reads InstallLocation from
; the uninstall key Inno names after it, to tell an installed copy from one
; running out of a build folder. Change it in one place only and updates go
; quiet.
AppId={{8B4A17D2-3C61-4F0E-9E5B-2A7D6C914F83}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Publisher}
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
VersionInfoVersion={#AppVersion}

DefaultDirName={autopf}\FileManager
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir=..\dist
OutputBaseFilename=FileManager-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes

; An update runs this installer with /SILENT while the old version may still be
; shutting down. Inno waits for the executable rather than failing on a locked
; file, and does not relaunch anything afterwards -- the application decides
; when it restarts, not the installer.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\FileManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Not shown during a silent run, which is what an update is.
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The staged installer the updater downloads. Settings in %APPDATA% are left
; alone: an uninstall is usually a reinstall, and losing the pane paths for it
; is a small insult with no benefit.
Type: filesandordirs; Name: "{localappdata}\FileManager\updates"
