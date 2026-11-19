#define AppName "Desktop Widgets"
#ifndef AppVersion
  #define AppVersion "1.0.2"
#endif
#define AppPublisher "Desktop Widgets contributors"

[Setup]
AppId={{96E98D89-6531-4C80-B3D5-7126CDA5B683}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\DesktopWidgets
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=DesktopWidgets-Setup-{#AppVersion}
LicenseFile=..\LICENSE
InfoAfterFile=..\THIRD_PARTY_NOTICES.md
SetupIconFile=..\todo-widget\app\todo_icon.ico
UninstallDisplayIcon={app}\unins000.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "full"; Description: "All widgets"
Name: "custom"; Description: "Choose widgets"; Flags: iscustom

[Components]
Name: "todo"; Description: "To-Do Widget (tasks and SMART goals)"; Types: full custom
Name: "usage"; Description: "Usage Monitor (AI plan limits and cloud budgets)"; Types: full custom
Name: "monitor"; Description: "Desktop Monitor (CPU, GPU, RAM and disk card)"; Types: full custom
Name: "monitor\cputemp"; Description: "CPU temperature sensor (installs elevated helper task and PawnIO driver; asks for administrator approval once)"; Flags: dontinheritcheck

[Tasks]
Name: "startup"; Description: "Start selected widgets automatically when I sign in"; GroupDescription: "Startup:"; Flags: checkedonce
Name: "desktopicon"; Description: "Create desktop shortcuts for selected widgets"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\DesktopWidgets\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: todo or usage or monitor
Source: "..\dist\DesktopWidgets\TodoWidget.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: todo
Source: "..\dist\DesktopWidgets\UsageMonitor.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: usage
Source: "..\dist\DesktopWidgets\DesktopMonitor.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: monitor
Source: "..\dist\CpuTempHelper\*"; DestDir: "{app}\CpuTempHelper"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: monitor\cputemp
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\To-Do Widget"; Filename: "{app}\TodoWidget.exe"; Components: todo
Name: "{autoprograms}\Usage Monitor"; Filename: "{app}\UsageMonitor.exe"; Components: usage
Name: "{autoprograms}\Desktop Monitor"; Filename: "{app}\DesktopMonitor.exe"; Components: monitor
Name: "{autodesktop}\To-Do Widget"; Filename: "{app}\TodoWidget.exe"; Components: todo; Tasks: desktopicon
Name: "{autodesktop}\Usage Monitor"; Filename: "{app}\UsageMonitor.exe"; Components: usage; Tasks: desktopicon
Name: "{autodesktop}\Desktop Monitor"; Filename: "{app}\DesktopMonitor.exe"; Components: monitor; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DesktopWidgets.TodoWidget"; ValueData: """{app}\TodoWidget.exe"""; Flags: uninsdeletevalue; Components: todo; Tasks: startup
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DesktopWidgets.UsageMonitor"; ValueData: """{app}\UsageMonitor.exe"""; Flags: uninsdeletevalue; Components: usage; Tasks: startup
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DesktopWidgets.DesktopMonitor"; ValueData: """{app}\DesktopMonitor.exe"""; Flags: uninsdeletevalue; Components: monitor; Tasks: startup

[Run]
Filename: "{app}\TodoWidget.exe"; Description: "Launch To-Do Widget"; Flags: nowait postinstall skipifsilent; Components: todo
Filename: "{app}\UsageMonitor.exe"; Description: "Launch Usage Monitor"; Flags: nowait postinstall skipifsilent; Components: usage
Filename: "{app}\DesktopMonitor.exe"; Description: "Launch Desktop Monitor"; Flags: nowait postinstall skipifsilent; Components: monitor

[Code]
var
  DeleteUserData: Boolean;

procedure StopRunningWidgets();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'),
    '/f /im TodoWidget.exe /im UsageMonitor.exe /im DesktopMonitor.exe',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function TargetUserName(): String;
var
  Domain: String;
begin
  // Inno Setup has no userdomain constant; read the environment variable instead.
  Domain := GetEnv('USERDOMAIN');
  Result := ExpandConstant('{username}');
  if Domain <> '' then
    Result := Domain + '\' + Result;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
  InstalledFlag: Cardinal;
  Launched, Registered: Boolean;
begin
  if CurStep = ssInstall then
    StopRunningWidgets();
  if (CurStep = ssPostInstall) and WizardIsComponentSelected('monitor\cputemp') then
  begin
    InstalledFlag := 0;
    Launched := ShellExec('runas', ExpandConstant('{app}\CpuTempHelper\CpuTempHelper.exe'),
      '--install --download-pawnio --user "' + TargetUserName() + '"',
      ExpandConstant('{app}\CpuTempHelper'), SW_HIDE, ewWaitUntilTerminated, ErrorCode);
    Registered := RegQueryDWordValue(HKLM64, 'SOFTWARE\DesktopWidgets\CpuTempHelper',
      'Installed', InstalledFlag);
    if ((not Launched) or (not Registered) or (InstalledFlag <> 1)) and (not WizardSilent()) then
      MsgBox('The CPU temperature helper was not enabled. Administrator approval may have been declined.' + #13#10 +
        'Desktop Monitor will show a dash for CPU temperature. The other indicators still work.',
        mbInformation, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  HelperPath, HelperDir, RunKey: String;
  ErrorCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    StopRunningWidgets();
    RunKey := 'Software\Microsoft\Windows\CurrentVersion\Run';
    RegDeleteValue(HKCU, RunKey, 'DesktopWidgets.TodoWidget');
    RegDeleteValue(HKCU, RunKey, 'DesktopWidgets.UsageMonitor');
    RegDeleteValue(HKCU, RunKey, 'DesktopWidgets.DesktopMonitor');
    HelperDir := ExpandConstant('{commonpf64}\DesktopWidgets\CpuTempHelper');
    HelperPath := HelperDir + '\CpuTempHelper.exe';
    if FileExists(HelperPath) then
      ShellExec('runas', HelperPath, '--uninstall', HelperDir,
        SW_HIDE, ewWaitUntilTerminated, ErrorCode);
    DeleteUserData := False;
    if not UninstallSilent then
      DeleteUserData := MsgBox('Also delete your saved tasks, goals, provider settings and saved API keys?' + #13#10 +
        'Choose No to keep your data for a future installation.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    if DeleteUserData and FileExists(ExpandConstant('{app}\UsageMonitor.exe')) then
    begin
      { Purge credentials while provider configuration still exists. }
      if not Exec(ExpandConstant('{app}\UsageMonitor.exe'), '--purge-secrets',
        ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ErrorCode) then
        DeleteUserData := False
      else if ErrorCode <> 0 then
        DeleteUserData := False;
      if not DeleteUserData then
        MsgBox('Saved API keys could not be removed. Your settings have been kept so you can retry.',
          mbInformation, MB_OK);
    end;
  end;
  if (CurUninstallStep = usPostUninstall) and DeleteUserData then
    DelTree(ExpandConstant('{userappdata}\DesktopWidgets'), True, True, True);
end;
