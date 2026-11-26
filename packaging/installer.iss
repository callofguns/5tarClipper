; Inno Setup script for the Windows installer.
; Build (after PyInstaller):  ISCC.exe /DAppVersion=0.5-Beta packaging\installer.iss
; Output: dist\5tarClipper-Setup.exe

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
; Same AppId as when the app was called "GTA VI Clipper", so this upgrades that install.
AppId={{6B1E3F2A-9C4D-4E8B-A7F1-2D5C8E9B0A13}
AppName=5tarClipper
AppVersion={#AppVersion}
AppPublisher=callofguns
AppPublisherURL=https://github.com/callofguns/GTA6-Clipper
DefaultDirName={localappdata}\Programs\5tarClipper
; Move installs from the old "GTA6 Clipper" folder into the new one (the old folder is removed below)
UsePreviousAppDir=no
DefaultGroupName=5tarClipper
; Don't reuse the old "GTA VI Clipper" Start Menu folder name from earlier installs
UsePreviousGroup=no
DisableProgramGroupPage=yes
; Installs for the current user only: no admin password needed
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=5tarClipper-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\5tarClipper.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; Leftovers from when the app was called "GTA VI Clipper" (program files and shortcuts only;
; settings and clips are moved to the new name by the app itself on first launch)
Type: filesandordirs; Name: "{localappdata}\Programs\GTA6 Clipper"
Type: filesandordirs; Name: "{userprograms}\GTA VI Clipper"
Type: files; Name: "{userdesktop}\GTA VI Clipper.lnk"

[Files]
Source: "..\dist\5tarClipper\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\5tarClipper"; Filename: "{app}\5tarClipper.exe"
Name: "{group}\Uninstall 5tarClipper"; Filename: "{uninstallexe}"
Name: "{userdesktop}\5tarClipper"; Filename: "{app}\5tarClipper.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\5tarClipper.exe"; Description: "Start 5tarClipper now"; Flags: nowait postinstall skipifsilent
; One-click updates run the installer silently with /RELAUNCH=1 so the app reopens afterwards
Filename: "{app}\5tarClipper.exe"; Flags: nowait; Check: RelaunchAfterUpdate

[UninstallDelete]
; Settings, caches and downloaded tools (FFmpeg, Deno, GPU files). Your clips in Videos are kept.
Type: filesandordirs; Name: "{localappdata}\5tarClipper"
Type: filesandordirs; Name: "{localappdata}\GTA6 Clipper"

[Code]
function RelaunchAfterUpdate: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;
