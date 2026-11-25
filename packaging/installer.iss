; Inno Setup script for the Windows installer.
; Build (after PyInstaller):  ISCC.exe /DAppVersion=0.1.0 packaging\installer.iss
; Output: dist\GTA6-Clipper-Setup.exe

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
AppId={{6B1E3F2A-9C4D-4E8B-A7F1-2D5C8E9B0A13}
AppName=GTA VI Clipper
AppVersion={#AppVersion}
AppPublisher=callofguns
AppPublisherURL=https://github.com/callofguns/gta6-clipper
DefaultDirName={localappdata}\Programs\GTA6 Clipper
DefaultGroupName=GTA VI Clipper
DisableProgramGroupPage=yes
; Installs for the current user only: no admin password needed
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=GTA6-Clipper-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\GTA6 Clipper.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\GTA6 Clipper\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\GTA VI Clipper"; Filename: "{app}\GTA6 Clipper.exe"
Name: "{group}\Uninstall GTA VI Clipper"; Filename: "{uninstallexe}"
Name: "{userdesktop}\GTA VI Clipper"; Filename: "{app}\GTA6 Clipper.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\GTA6 Clipper.exe"; Description: "Start GTA VI Clipper now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Settings, caches and downloaded tools (FFmpeg, Deno, GPU files). Your clips in Videos are kept.
Type: filesandordirs; Name: "{localappdata}\GTA6 Clipper"
