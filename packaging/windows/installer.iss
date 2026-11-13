; Inno Setup script for the Windows installer. Compiled by build.py:
;   ISCC.exe /DAppVersion=v1.2.0 /DRootDir=<repo> /DSourceDir=<PyInstaller folder>
;            /DOutputDir=<dist> /DOutputName=<file name without .exe> installer.iss
; Installs per user (no administrator rights needed).

#ifndef AppVersion
  #define AppVersion "dev"
#endif

[Setup]
AppId={{754E43B4-6AAE-4DFE-9E79-3FB48613105F}
AppName=Garmin Export
AppVersion={#AppVersion}
AppPublisher=Garmin Export contributors
AppPublisherURL=https://github.com/marcbnf/garmin-export
AppSupportURL=https://github.com/marcbnf/garmin-export/issues
DefaultDirName={localappdata}\Programs\Garmin Export
DefaultGroupName=Garmin Export
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile={#RootDir}\garmin_export\assets\icon.ico
UninstallDisplayIcon={app}\GarminExport.exe
LicenseFile={#RootDir}\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Garmin Export"; Filename: "{app}\GarminExport.exe"
Name: "{autodesktop}\Garmin Export"; Filename: "{app}\GarminExport.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\GarminExport.exe"; Description: "{cm:LaunchProgram,Garmin Export}"; Flags: nowait postinstall skipifsilent
