#ifndef PayloadDir
  #error PayloadDir is required
#endif
#ifndef SeedDir
  #error SeedDir is required
#endif
#ifndef OutputPath
  #error OutputPath is required
#endif

[Setup]
AppId={{CC340B42-F94C-4F3F-B250-6148294B287F}
AppName=Wabot
AppVersion=1.10.0
AppPublisher=Wabot
DefaultDirName={localappdata}\Programs\Wabot
DefaultGroupName=Wabot
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableProgramGroupPage=yes
WizardStyle=modern
OutputDir={#OutputPath}
OutputBaseFilename=WabotSetup
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\WabotLauncher.exe
AppMutex=Local\WabotDesktop
SetupMutex=Local\WabotSetup
CloseApplications=no
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut to the launcher"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SeedDir}\config.json"; DestDir: "{tmp}\WabotSeed"; Flags: ignoreversion deleteafterinstall skipifsourcedoesntexist
Source: "{#SeedDir}\bot.sqlite3"; DestDir: "{tmp}\WabotSeed"; Flags: ignoreversion deleteafterinstall skipifsourcedoesntexist
Source: "{#SeedDir}\credentials.json"; DestDir: "{tmp}\WabotSeed"; Flags: ignoreversion deleteafterinstall skipifsourcedoesntexist

[Icons]
Name: "{group}\Wabot Launcher"; Filename: "{app}\WabotLauncher.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Wabot Launcher"; Filename: "{app}\WabotLauncher.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\WabotLauncher.exe"; Description: "Open Wabot Launcher"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  ProfileDir: String;
  Params: String;
begin
  if CurStep = ssPostInstall then
  begin
    ProfileDir := ExpandConstant('{param:DataDir|{localappdata}\Wabot}');
    Params := '--bootstrap ' + AddQuotes(ExpandConstant('{tmp}\WabotSeed')) +
              ' --data-dir ' + AddQuotes(ProfileDir);
    if not Exec(ExpandConstant('{app}\WabotWorker.exe'), Params, ExpandConstant('{app}'),
                SW_HIDE, ewWaitUntilTerminated, Code) then
      RaiseException('Could not prepare the Wabot configuration.');
    if Code <> 0 then
      RaiseException('Preparing the data failed. Check the data folder and close any running copy of the bot.');
  end;
end;
