; Dental Intake — Windows installer (Inno Setup 6).
; Built by desktop\build_windows.ps1:  iscc /DAppVersion=1.0.0 /DSourceDir=<program folder> DentalIntake.iss
;
; Silent install (IT / automation):
;   Main PC:   DentalIntakeSetup.exe /VERYSILENT /ROLE=server /OFFICE="Smile Dental" /ADMINNAME="Olivia Owner"
;              /ADMINEMAIL=owner@smile.com [/TZ=America/Chicago] [/BACKUPDIR=\\nas\share\DentalIntake]
;              [/HTTPSPORT=443] [/RESULTFILE=C:\path\result.txt]
;   Other PCs: DentalIntakeSetup.exe /VERYSILENT /ROLE=workstation /SERVER=FRONTDESK-PC /CACODE=ABCD-1234-EF56

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\DentalIntake"
#endif

[Setup]
AppId={{6D3C2B8E-8A7F-4F7B-9B5E-2C1D4E5F6A70}
AppName=Dental Intake
AppVersion={#AppVersion}
AppPublisher=Dental Intake
DefaultDirName={autopf}\Dental Intake
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=DentalIntakeSetup
SetupIconFile={#SourceDir}\icon.ico
UninstallDisplayIcon={app}\Dental Intake.exe
UninstallDisplayName=Dental Intake
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
CloseApplications=yes
MinVersion=10.0

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Dental Intake"; Filename: "{app}\Dental Intake.exe"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\Dental Intake"; Filename: "{app}\Dental Intake.exe"; IconFilename: "{app}\icon.ico"
Name: "{commonstartup}\Dental Intake tray icon"; Filename: "{app}\Dental Intake.exe"; Parameters: "tray"; IconFilename: "{app}\icon.ico"
Name: "{autoprograms}\Dental Intake Office Guide"; Filename: "{app}\OFFICE_GUIDE.html"

[Run]
Filename: "{app}\Dental Intake.exe"; Parameters: "tray"; Flags: nowait postinstall runasoriginaluser skipifsilent; Description: "Show the Dental Intake tray icon"
Filename: "{app}\Dental Intake.exe"; Flags: nowait postinstall runasoriginaluser skipifsilent; Description: "Open Dental Intake now"

[UninstallRun]
Filename: "{app}\DentalIntakeService.exe"; Parameters: "stop"; RunOnceId: "StopService"; Flags: runhidden waituntilterminated; Check: SelectedServer
Filename: "{app}\DentalIntakeService.exe"; Parameters: "uninstall"; RunOnceId: "RemoveService"; Flags: runhidden waituntilterminated; Check: SelectedServer
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Dental Intake HTTPS"""; RunOnceId: "FwHttps"; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Dental Intake HTTP"""; RunOnceId: "FwHttp"; Flags: runhidden
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\remove-office-ca.ps1"""; RunOnceId: "RemoveCA"; Flags: runhidden waituntilterminated

[UninstallDelete]
Type: files; Name: "{app}\client.json"

[Code]
const
  RegKey = 'Software\Dental Intake';

var
  RolePage: TInputOptionWizardPage;
  OfficePage: TInputQueryWizardPage;
  BackupPage: TInputDirWizardPage;
  AccountPage: TInputQueryWizardPage;
  ServerPage: TInputQueryWizardPage;
  DonePage: TOutputMsgMemoWizardPage;
  IsUpgrade: Boolean;
  ExistingRole: String;
  WorkstationAddress: String;
  CaCode: String;

function DataDir(): String;
begin
  Result := ExpandConstant('{commonappdata}\Dental Intake');
end;

function Param(Name, Default: String): String;
begin
  Result := ExpandConstant('{param:' + Name + '|' + Default + '}');
end;

function SelectedServer(): Boolean;
begin
  if IsUpgrade then
    Result := ExistingRole <> 'workstation'
  else
    Result := RolePage.SelectedValueIndex = 0;
end;

function JsonEscape(S: String): String;
begin
  StringChangeEx(S, '\', '\\', True);
  StringChangeEx(S, '"', '\"', True);
  Result := S;
end;

procedure InitializeWizard();
begin
  IsUpgrade := RegQueryStringValue(HKLM, RegKey, 'Role', ExistingRole);

  RolePage := CreateInputOptionPage(wpSelectDir, 'How will this PC be used?',
    'Install on the main office PC first, then on every other PC.',
    'Choose one:', True, False);
  RolePage.Add('This PC stores the office''s data (the main front-desk PC; install here first)');
  RolePage.Add('Connect to the office PC that stores the data (every other PC)');
  if Param('ROLE', 'server') = 'workstation' then RolePage.SelectedValueIndex := 1 else RolePage.SelectedValueIndex := 0;

  OfficePage := CreateInputQueryPage(RolePage.ID, 'Your office',
    'These create the first administrator account.',
    'You will get a one-time temporary password at the end of setup.');
  OfficePage.Add('Office name (shown to patients):', False);
  OfficePage.Add('Administrator full name:', False);
  OfficePage.Add('Administrator email (used to sign in):', False);
  OfficePage.Add('Time zone (leave blank to use this PC''s):', False);
  OfficePage.Values[0] := Param('OFFICE', '');
  OfficePage.Values[1] := Param('ADMINNAME', '');
  OfficePage.Values[2] := Param('ADMINEMAIL', '');
  OfficePage.Values[3] := Param('TZ', '');

  BackupPage := CreateInputDirPage(OfficePage.ID, 'Nightly backups',
    'Where should the nightly backup be saved?',
    'To keep a copy off this PC, choose your shared drive. Use its network path, for example ' +
    '\\NAS\DentalIntake\Backups (not a drive letter like Z:). The database itself always stays on this PC.',
    False, '');
  BackupPage.Add('Backup folder:');
  BackupPage.Values[0] := Param('BACKUPDIR', ExpandConstant('{commonappdata}\Dental Intake\backups'));

  AccountPage := CreateInputQueryPage(BackupPage.ID, 'Shared drive access (optional)',
    'Only needed if backups go to a shared network drive.',
    'Enter a Windows account that can write to that shared drive (for example OFFICE\frontdesk). ' +
    'Leave blank if backups stay on this PC or the drive allows this computer to write.');
  AccountPage.Add('Windows account (DOMAIN\user or .\user):', False);
  AccountPage.Add('Password:', True);
  AccountPage.Values[0] := Param('SVCUSER', '');

  ServerPage := CreateInputQueryPage(RolePage.ID, 'Connect to the office PC',
    'Enter the address of the main Dental Intake PC.',
    'On the main PC, find it in the tray icon (by the clock) or in the setup summary, e.g. FRONTDESK-PC or 192.168.1.50.');
  ServerPage.Add('Office PC name or address:', False);
  ServerPage.Values[0] := Param('SERVER', '');

  DonePage := CreateOutputMsgMemoPage(wpInfoAfter, 'Dental Intake is ready',
    'Please read this, and write down the temporary password.', '', '');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = RolePage.ID) then Result := IsUpgrade;
  if (PageID = OfficePage.ID) or (PageID = BackupPage.ID) or (PageID = AccountPage.ID) then
    Result := IsUpgrade or (not SelectedServer());
  if (PageID = ServerPage.ID) then Result := IsUpgrade or SelectedServer();
end;

function FormatCode(Hex: String): String;
begin
  Hex := Uppercase(Hex);
  Result := Copy(Hex, 1, 4) + '-' + Copy(Hex, 5, 4) + '-' + Copy(Hex, 9, 4);
end;

function HostPart(Addr: String): String;
var P: Integer;
begin
  Result := Addr;
  P := Pos(':', Result);
  if P > 0 then Result := Copy(Result, 1, P - 1);
end;

function IsCertFile(Path: String): Boolean;
var Content: AnsiString;
begin
  Result := LoadStringFromFile(Path, Content) and (Pos('-----BEGIN CERTIFICATE-----', Content) = 1);
end;

function TryFetch(Url: String): Boolean;
begin
  Result := False;
  try
    DownloadTemporaryFile(Url, 'office-ca.crt', '', nil);
    // Another web server on port 80 may answer; only accept an actual certificate.
    Result := IsCertFile(ExpandConstant('{tmp}\office-ca.crt'));
  except
    Result := False;
  end;
end;

function FetchOfficeCA(Addr: String): Boolean;
begin
  // The main PC serves its certificate on port 80, or 8080 if 80 was taken.
  Result := TryFetch('http://' + HostPart(Addr) + '/office-ca.crt');
  if not Result then
    Result := TryFetch('http://' + HostPart(Addr) + ':8080/office-ca.crt');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var Expected: String;
begin
  Result := True;
  if CurPageID = OfficePage.ID then begin
    if (Trim(OfficePage.Values[0]) = '') or (Trim(OfficePage.Values[1]) = '') or (Pos('@', OfficePage.Values[2]) < 2) then begin
      MsgBox('Please enter the office name, the administrator''s name and a valid email address.', mbError, MB_OK);
      Result := False;
    end;
  end;
  if CurPageID = BackupPage.ID then begin
    if (Length(BackupPage.Values[0]) >= 2) and (BackupPage.Values[0][2] = ':') and
       (Uppercase(Copy(BackupPage.Values[0], 1, 1)) <> 'C') and (Pos('\\', BackupPage.Values[0]) <> 1) then
      if not WizardSilent() then
        Result := MsgBox('If ' + Copy(BackupPage.Values[0], 1, 2) + ' is a mapped network drive, the background service ' +
          'cannot see drive letters. Use the \\server\share path instead.' + #13#10#13#10 + 'Continue anyway?',
          mbConfirmation, MB_YESNO) = IDYES;
  end;
  if CurPageID = ServerPage.ID then begin
    WorkstationAddress := Trim(ServerPage.Values[0]);
    if WorkstationAddress = '' then begin
      MsgBox('Enter the main office PC''s name or address.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if not FetchOfficeCA(WorkstationAddress) then begin
      MsgBox('Could not reach Dental Intake on ' + WorkstationAddress + '.' + #13#10 +
        'Check that the main PC is on, Dental Intake is installed there, and both PCs are on the office network.',
        mbError, MB_OK);
      Result := False;
      Exit;
    end;
    CaCode := FormatCode(GetSHA256OfFile(ExpandConstant('{tmp}\office-ca.crt')));
    if WizardSilent() then begin
      Expected := Uppercase(Param('CACODE', ''));
      Result := (Expected <> '') and (Expected = CaCode);
      if not Result then Log('Office security code mismatch or /CACODE missing: got ' + CaCode);
    end else
      Result := MsgBox('Office security code: ' + CaCode + #13#10#13#10 +
        'Does this exactly match the security code shown on the main PC ' +
        '(tray icon > "Office security code")?' + #13#10#13#10 +
        'Only continue if it matches. A different code could mean someone is impersonating the office PC.',
        mbConfirmation, MB_YESNO) = IDYES;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer;
begin
  Result := '';
  // Upgrading: stop the service so its files can be replaced.
  if FileExists(ExpandConstant('{app}\DentalIntakeService.exe')) then
    Exec(ExpandConstant('{app}\DentalIntakeService.exe'), 'stop', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function RunHidden(Exe, Params: String): Integer;
var Code: Integer;
begin
  if not Exec(Exe, Params, '', SW_HIDE, ewWaitUntilTerminated, Code) then Code := -1;
  Result := Code;
  // Never log the service-account password.
  if Pos('password=', Params) > 0 then Log('ran ' + Exe + ' (config service account) -> ' + IntToStr(Code))
  else Log('ran ' + Exe + ' ' + Params + ' -> ' + IntToStr(Code));
end;

function ValueOf(Lines: TArrayOfString; Key: String): String;
var I: Integer;
begin
  Result := '';
  for I := 0 to GetArrayLength(Lines) - 1 do
    if Pos(Key + '=', Lines[I]) = 1 then Result := Copy(Lines[I], Length(Key) + 2, MaxInt);
end;

function WaitForHealth(Port: String): Boolean;
var I: Integer; Url: String;
begin
  Result := False;
  if Port = '443' then Url := 'https://localhost/api/health' else Url := 'https://localhost:' + Port + '/api/health';
  // Uses the Windows certificate store, so this also proves the office certificate is trusted.
  for I := 1 to 45 do begin
    if RunHidden('powershell.exe', '-NoProfile -Command "try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 ' +
      Url + ').StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"') = 0 then begin
      Result := True;
      Exit;
    end;
    Sleep(2000);
  end;
end;

procedure InstallServer();
var
  Args, ResultFile, Account, Summary, NetworkAddr, CaUrl: String;
  Lines: TArrayOfString;
  Code: Integer;
  Url, LocalUrl, HttpsPort, HttpPort, Code2, Temp, CaFile, BackupDir: String;
begin
  ForceDirectories(DataDir());
  // Office data is readable only by Windows administrators and the service.
  RunHidden(ExpandConstant('{sys}\icacls.exe'), AddQuotes(DataDir()) + ' /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F /T /Q');

  ResultFile := ExpandConstant('{tmp}\setup-result.txt');
  Args := 'setup --result-file ' + AddQuotes(ResultFile);
  if not IsUpgrade then begin
    Args := Args + ' --office-name ' + AddQuotes(OfficePage.Values[0]) + ' --admin-name ' + AddQuotes(OfficePage.Values[1]) +
      ' --admin-email ' + AddQuotes(Trim(OfficePage.Values[2])) + ' --backup-dir ' + AddQuotes(BackupPage.Values[0]);
    if Trim(OfficePage.Values[3]) <> '' then Args := Args + ' --timezone ' + AddQuotes(Trim(OfficePage.Values[3]));
    if Param('HTTPSPORT', '') <> '' then Args := Args + ' --https-port ' + Param('HTTPSPORT', '');
  end;
  Code := RunHidden(ExpandConstant('{app}\DentalIntakeServer.exe'), Args);
  if (Code <> 0) or (not LoadStringsFromFile(ResultFile, Lines)) then begin
    MsgBox('Setup could not finish (error ' + IntToStr(Code) + '). See ' + DataDir() + '\logs.', mbError, MB_OK);
    Exit;
  end;
  Url := ValueOf(Lines, 'url');
  LocalUrl := ValueOf(Lines, 'local_url');
  HttpsPort := ValueOf(Lines, 'https_port');
  HttpPort := ValueOf(Lines, 'http_port');
  Code2 := ValueOf(Lines, 'security_code');
  Temp := ValueOf(Lines, 'temporary_password');
  CaFile := ValueOf(Lines, 'ca_file');
  DeleteFile(ResultFile);

  RunHidden(ExpandConstant('{sys}\certutil.exe'), '-addstore -f Root ' + AddQuotes(CaFile));
  RunHidden(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name="Dental Intake HTTPS"');
  RunHidden(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name="Dental Intake HTTP"');
  RunHidden(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall add rule name="Dental Intake HTTPS" dir=in action=allow protocol=TCP localport=' + HttpsPort + ' profile=private,domain');
  RunHidden(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall add rule name="Dental Intake HTTP" dir=in action=allow protocol=TCP localport=' + HttpPort + ' profile=private,domain');

  if IsUpgrade then BackupDir := '' else BackupDir := BackupPage.Values[0];
  SaveStringToFile(ExpandConstant('{app}\client.json'),
    '{"role": "server", "url": "' + JsonEscape(LocalUrl) + '", "network_url": "' + JsonEscape(Url) +
    '", "backup_dir": "' + JsonEscape(BackupDir) + '", "security_code": "' + Code2 + '"}', False);
  RegWriteStringValue(HKLM, RegKey, 'Role', 'server');

  if not IsUpgrade then begin
    RunHidden(ExpandConstant('{app}\DentalIntakeService.exe'), 'install');
    Account := Trim(AccountPage.Values[0]);
    if Account <> '' then begin
      RunHidden('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -File ' + AddQuotes(ExpandConstant('{app}\grant-service-logon.ps1')) +
        ' -Account ' + AddQuotes(Account) + ' -DataDir ' + AddQuotes(DataDir()));
      RunHidden(ExpandConstant('{sys}\sc.exe'), 'config DentalIntake obj= ' + AddQuotes(Account) + ' password= ' + AddQuotes(AccountPage.Values[1]));
    end;
  end;
  RunHidden(ExpandConstant('{app}\DentalIntakeService.exe'), 'start');
  if not WaitForHealth(HttpsPort) then
    MsgBox('Dental Intake was installed but did not respond yet. It may need a minute; if it still does not open, ' +
      'see ' + DataDir() + '\logs.', mbInformation, MB_OK);

  NetworkAddr := GetComputerNameString();
  if HttpsPort <> '443' then NetworkAddr := NetworkAddr + ':' + HttpsPort;
  CaUrl := 'http://' + GetComputerNameString();
  if HttpPort <> '80' then CaUrl := CaUrl + ':' + HttpPort;
  CaUrl := CaUrl + '/office-ca.crt';
  Summary := 'Dental Intake is installed and running on this PC.' + #13#10#13#10;
  if Temp <> '' then
    Summary := Summary + 'FIRST SIGN-IN' + #13#10 +
      '  Email:               ' + Trim(OfficePage.Values[2]) + #13#10 +
      '  Temporary password:  ' + Temp + #13#10 +
      '  (Shown only once. You will choose your own password and set up an authenticator app.)' + #13#10#13#10;
  Summary := Summary +
    'Open it with the "Dental Intake" icon on the desktop.' + #13#10#13#10 +
    'IMPORTANT - SAVE THE ENCRYPTION KEY' + #13#10 +
    '  Right-click the tooth icon by the clock > "Save encryption key to USB...".' + #13#10 +
    '  Keep the USB in the office safe. Without it, backups cannot be read.' + #13#10#13#10 +
    'OTHER OFFICE PCs' + #13#10 +
    '  Run this same installer, choose "Connect to the office PC", and enter:  ' + NetworkAddr + #13#10 +
    '  Office security code to compare:  ' + Code2 + #13#10#13#10 +
    'TABLETS' + #13#10 +
    '  Patient links open at ' + Url + #13#10 +
    '  On each office tablet, open ' + CaUrl + ' once and install it.';
  DonePage.RichEditViewer.Lines.Text := Summary;
  if Param('RESULTFILE', '') <> '' then
    SaveStringToFile(Param('RESULTFILE', ''), 'temporary_password=' + Temp + #13#10 + 'url=' + Url + #13#10 +
      'local_url=' + LocalUrl + #13#10 + 'security_code=' + Code2 + #13#10 + 'network_address=' + NetworkAddr + #13#10 +
      'ca_http_port=' + HttpPort + #13#10, False);
end;

procedure InstallWorkstation();
var Addr: String;
begin
  if IsUpgrade then begin
    DonePage.RichEditViewer.Lines.Text := 'Dental Intake was updated.';
    Exit;
  end;
  RunHidden(ExpandConstant('{sys}\certutil.exe'), '-addstore -f Root ' + AddQuotes(ExpandConstant('{tmp}\office-ca.crt')));
  Addr := WorkstationAddress;
  SaveStringToFile(ExpandConstant('{app}\client.json'),
    '{"role": "workstation", "url": "https://' + JsonEscape(Addr) + '/staff", "security_code": "' + CaCode + '"}', False);
  RegWriteStringValue(HKLM, RegKey, 'Role', 'workstation');
  DonePage.RichEditViewer.Lines.Text :=
    'This PC is connected to Dental Intake on ' + Addr + '.' + #13#10#13#10 +
    'Open it with the "Dental Intake" icon on the desktop and sign in with your own staff account.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if SelectedServer() then InstallServer() else InstallWorkstation();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then begin
    RegDeleteKeyIncludingSubkeys(HKLM, RegKey);
    if DirExists(ExpandConstant('{commonappdata}\Dental Intake')) and not UninstallSilent() then
      MsgBox('Your office data (patient records, encryption key and backups) was kept in ' +
        ExpandConstant('{commonappdata}\Dental Intake') + '. Reinstalling Dental Intake will use it again.',
        mbInformation, MB_OK);
  end;
end;
