#ifndef ProductName
  #define ProductName "Q-Pixelate"
#endif
#ifndef StateRoot
  #define StateRoot "{localappdata}\Q-Pixelate"
#endif

[Setup]
AppId={#ProductName}
AppName={#ProductName}
AppVersion={#ProductVersion}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
DefaultDirName={code:DefaultProgramDirectory}
UsePreviousAppDir=no
DisableDirPage=no
DefaultGroupName={#ProductName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\Q-Pixelate.exe
OutputBaseFilename=Q-Pixelate-Setup
SetupMutex={#ProductName}-Setup
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "zh"; MessagesFile: "compiler:Default.isl"

[LangOptions]
zh.LanguageName=简体中文
zh.LanguageID=$0804

[Messages]
en.WizardSelectDir=Program location
en.SelectDirDesc=Choose where to install the launcher and uninstaller.
zh.WizardSelectDir=程序安装位置
zh.SelectDirDesc=选择启动器与卸载程序的存放目录。
zh.SelectDirLabel3=安装程序将把程序文件放在下列目录中。
zh.ButtonBack=< 上一步
zh.ButtonNext=下一步 >
zh.ButtonInstall=安装
zh.ButtonCancel=取消
zh.ButtonFinish=完成
zh.ButtonBrowse=浏览…
zh.ButtonWizardBrowse=浏览…
zh.WizardReady=准备安装
zh.ReadyLabel1=安装程序已准备好安装 [name]。
zh.ReadyLabel2a=单击「安装」继续，或单击「上一步」检查或更改位置。
zh.FinishedHeadingLabel=安装完成
zh.FinishedLabelNoIcons=已完成 [name] 的安装。
zh.FinishedLabel=已完成 [name] 的安装。可从开始菜单启动程序。
zh.ConfirmUninstall=要卸载 %1 吗？

[CustomMessages]
en.ComponentTitle=Component location
zh.ComponentTitle=组件安装位置
en.ComponentDescription=Choose where to store the editor, FFmpeg, and optional recognition components.
zh.ComponentDescription=选择编辑器、FFmpeg 和可选识别组件的存放目录。
en.ComponentPrompt=The launcher can install components from matching ZIPs next to Setup or from a folder you select when prompted.
zh.ComponentPrompt=启动器可从安装包旁边的配套 ZIP 或稍后指定的文件夹安装组件。
en.BadComponentDirectory=Choose an absolute, writable component directory separate from the program and installer cache.
zh.BadComponentDirectory=请选择可写的绝对路径，并与程序目录和安装包缓存分开。
en.SourceCollision=Choose another program location. The selected folder already contains “%1”.
zh.SourceCollision=请选择另一个程序目录。所选目录已包含“%1”。
en.BadProgramDirectory=Select a new program directory to move the installation.
zh.BadProgramDirectory=请选择新的程序目录以移动安装。
en.NoInstalledProgram=The current Q-Pixelate installation could not be located.
zh.NoInstalledProgram=无法找到当前的 Q-Pixelate 安装。
en.LayoutError=Setup could not read or update the per-user installation settings.
zh.LayoutError=无法读取或更新当前用户的安装设置。
en.CacheError=Setup could not save a copy of the installer for future maintenance.
zh.CacheError=无法保存用于日后维护的安装程序副本。
en.VerifyError=The launcher failed to start from the new program location.
zh.VerifyError=启动器无法从新的程序位置启动。
en.CleanupPrevious=The launcher started from the new location. Remove matching Q-Pixelate program files from the previous folder?%n%n%1
zh.CleanupPrevious=启动器已从新位置启动。是否清理旧目录中与安装包一致的 Q-Pixelate 程序文件？%n%n%1
en.InactiveUninstaller=This uninstaller belongs to a previous Q-Pixelate location. Use Windows Installed Apps to uninstall the active copy.
zh.InactiveUninstaller=这是旧位置的卸载程序。请在 Windows 的「已安装的应用」中卸载当前版本。
en.RemoveComponents=Remove Q-Pixelate components installed by the launcher? Choose No to keep them for a future installation. Personal files and offline ZIPs are kept.
zh.RemoveComponents=是否清理启动器安装的 Q-Pixelate 组件？选择「否」可保留组件供以后使用。个人文件和离线 ZIP 会保留。
en.ComponentRemovalError=Q-Pixelate could not finish removing its components. Uninstall has been stopped so you can retry.
zh.ComponentRemovalError=Q-Pixelate 未能完成组件清理。卸载已停止，可以重试。
en.LaunchProgram=Launch Q-Pixelate
zh.LaunchProgram=启动 Q-Pixelate

[Files]
Source: "{srcexe}"; DestDir: "{code:InstallerCacheDir}"; DestName: "{code:InstallerCacheName}"; Check: CacheNeedsCopy; Flags: external ignoreversion uninsneveruninstall; AfterInstall: CachedInstallerCreated
Source: "{#PortableDir}\Q-Pixelate.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PortableDir}\component-catalog.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PortableDir}\launcher-licenses\PyInstaller-COPYING.txt"; DestDir: "{app}\launcher-licenses"; Flags: ignoreversion
Source: "{#PortableDir}\launcher-licenses\CPython-LICENSE.txt"; DestDir: "{app}\launcher-licenses"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#PortableDir}\launcher-licenses\Tk-license.terms"; DestDir: "{app}\launcher-licenses"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#PortableDir}\README.md"; DestDir: "{app}"; DestName: "Q-Pixelate-README.md"; Flags: ignoreversion; AfterInstall: VerifyInstallation

[Icons]
Name: "{userprograms}\{#ProductName}"; Filename: "{app}\Q-Pixelate.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\Q-Pixelate.exe"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram}"; Flags: nowait postinstall skipifsilent unchecked

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#ProductName}_is1';
  CachePrefix = 'Q-Pixelate-Setup-';

var
  ComponentPage: TInputDirWizardPage;
  OldLayout: AnsiString;
  OldProgram, OldComponent, OldArchives, OldInstaller, OldUninstaller: String;
  NewProgram, NewComponent, NewArchives, NewInstaller, PendingLayout: String;
  Relocating, LayoutReplaced, InstallCommitted, CacheCreated, InstallNeedsCache: Boolean;

function MoveFileEx(const ExistingName, NewName: String; Flags: Cardinal): Boolean;
  external 'MoveFileExW@kernel32.dll stdcall';

function WindowsTempFile(const Directory, Prefix: String; Unique: Cardinal; Buffer: String): Cardinal;
  external 'GetTempFileNameW@kernel32.dll stdcall';

function CreateTempFile(const Directory: String): String;
var
  Buffer: String;
begin
  Result := '';
  Buffer := StringOfChar(#0, 260);
  if WindowsTempFile(Directory, 'QPX', 0, Buffer) <> 0 then
    Result := Copy(Buffer, 1, Pos(#0, Buffer) - 1);
end;

function StateDirectory: String;
begin
  Result := ExpandConstant('{#StateRoot}');
end;

function LayoutPath: String;
begin
  Result := AddBackslash(StateDirectory) + 'installation.json';
end;

function CacheDirectory: String;
begin
  Result := AddBackslash(StateDirectory) + 'installers';
end;

function InstallerCacheDir(Param: String): String;
begin
  Result := CacheDirectory;
end;

function InstallerCacheName(Param: String): String;
begin
  Result := ExtractFileName(NewInstaller);
end;

function CacheNeedsCopy: Boolean;
begin
  Result := InstallNeedsCache;
end;

procedure CachedInstallerCreated;
begin
  CacheCreated := True;
end;

function SamePath(const A, B: String): Boolean;
begin
  Result := (A <> '') and (B <> '') and
    (CompareText(AddBackslash(ExpandFileName(A)), AddBackslash(ExpandFileName(B))) = 0);
end;

function InsidePath(const Path, Parent: String): Boolean;
begin
  Result := Pos(Lowercase(AddBackslash(ExpandFileName(Parent))),
    Lowercase(AddBackslash(ExpandFileName(Path)))) = 1;
end;

function IsAbsoluteDirectory(const Path: String): Boolean;
begin
  Result := (Length(Path) > 3) and
    ((Copy(Path, 2, 2) = ':\') or (Copy(Path, 1, 2) = '\\'));
end;

function ReadLayoutField(const Layout: AnsiString; const Name: String): String;
var
  Document, Key: String;
  I, Code: Integer;
begin
  Result := '';
  Document := UTF8Decode(Layout);
  Key := '"' + Name + '"';
  I := Pos(Key, Document);
  if I = 0 then
    Exit;
  I := I + Length(Key);
  while (I <= Length(Document)) and (Document[I] <= ' ') do
    I := I + 1;
  if (I > Length(Document)) or (Document[I] <> ':') then
    Exit;
  I := I + 1;
  while (I <= Length(Document)) and (Document[I] <= ' ') do
    I := I + 1;
  if (I > Length(Document)) or (Document[I] <> '"') then
    Exit;
  I := I + 1;
  while I <= Length(Document) do
  begin
    case Document[I] of
      '"': Exit;
      '\':
        begin
          I := I + 1;
          if I > Length(Document) then
            Break;
          case Document[I] of
            '"', '\', '/': Result := Result + Document[I];
            'u':
              begin
                Code := StrToIntDef('$' + Copy(Document, I + 1, 4), -1);
                if Code < 32 then
                  Break;
                Result := Result + Chr(Code);
                I := I + 4;
              end;
          else
            Break;
          end;
        end;
    else
      Result := Result + Document[I];
    end;
    I := I + 1;
  end;
  Result := '';
end;

function EncodeJsonPath(Value: String): String;
begin
  StringChangeEx(Value, '\', '\\', True);
  StringChangeEx(Value, '"', '\"', True);
  Result := '"' + Value + '"';
end;

function WriteLayoutTemp(const Data: AnsiString): String;
begin
  Result := CreateTempFile(StateDirectory);
  if Result = '' then
    Exit;
  if not SaveStringToFile(Result, Data, False) then
  begin
    DeleteFile(Result);
    Result := '';
  end;
end;

function ReadUninstallValue(const Name: String): String;
begin
  Result := '';
  RegQueryStringValue(HKCU64, UninstallKey, Name, Result);
end;

function InitializeSetup: Boolean;
begin
  Relocating := ExpandConstant('{param:relocate|0}') = '1';
  Result := False;
  if FileExists(LayoutPath) then
  begin
    if LoadStringFromFile(LayoutPath, OldLayout) then
    begin
      OldProgram := ReadLayoutField(OldLayout, 'program_dir');
      OldComponent := ReadLayoutField(OldLayout, 'component_dir');
      OldArchives := ReadLayoutField(OldLayout, 'archive_dir');
      OldInstaller := ReadLayoutField(OldLayout, 'installer');
    end;
    if not (IsAbsoluteDirectory(OldProgram) and IsAbsoluteDirectory(OldComponent)) then
    begin
      SuppressibleMsgBox(CustomMessage('LayoutError'), mbError, MB_OK, IDOK);
      Exit;
    end;
  end;
  if Relocating and ((OldProgram = '') or
      not FileExists(AddBackslash(OldProgram) + 'Q-Pixelate.exe')) then
  begin
    SuppressibleMsgBox(CustomMessage('NoInstalledProgram'), mbError, MB_OK, IDOK);
    Exit;
  end;
  if SamePath(OldProgram, ReadUninstallValue('Inno Setup: App Path')) then
    OldUninstaller := RemoveQuotes(ReadUninstallValue('UninstallString'));
  Result := True;
end;

function DefaultProgramDirectory(Param: String): String;
begin
  Result := OldProgram;
  if Result = '' then
    Result := ExpandConstant('{localappdata}\Programs\{#ProductName}');
end;

procedure InitializeWizard;
begin
  ComponentPage := CreateInputDirPage(wpSelectDir, CustomMessage('ComponentTitle'),
    CustomMessage('ComponentDescription'), CustomMessage('ComponentPrompt'), False, '');
  ComponentPage.Add('');
  if OldComponent <> '' then
    ComponentPage.Values[0] := OldComponent
  else
    ComponentPage.Values[0] := ExpandConstant('{param:componentdir|}');
  if ComponentPage.Values[0] = '' then
    ComponentPage.Values[0] := AddBackslash(StateDirectory) + 'components';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = ComponentPage.ID) and (OldComponent <> '');
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := SetupMessage(msgWizardSelectDir) + NewLine + Space + WizardDirValue +
    NewLine + NewLine + CustomMessage('ComponentTitle') + NewLine + Space + ComponentPage.Values[0];
end;

function PayloadName(Index: Integer): String;
begin
  case Index of
    0: Result := 'Q-Pixelate.exe';
    1: Result := 'component-catalog.json';
    2: Result := 'Q-Pixelate-README.md';
    3: Result := 'launcher-licenses\PyInstaller-COPYING.txt';
    4: Result := 'launcher-licenses\CPython-LICENSE.txt';
    5: Result := 'launcher-licenses\Tk-license.terms';
  end;
end;

function DirectoryError: String;
var
  I: Integer;
  Name, Target: String;
begin
  Result := '';
  if Relocating and SamePath(NewProgram, OldProgram) then
    Result := CustomMessage('BadProgramDirectory')
  else if not IsAbsoluteDirectory(NewComponent) or InsidePath(NewProgram, NewComponent) or
      InsidePath(StateDirectory, NewComponent) or InsidePath(NewComponent, CacheDirectory) or
      InsidePath(NewProgram, CacheDirectory) or SamePath(NewProgram, StateDirectory) then
    Result := CustomMessage('BadComponentDirectory')
  else if not SamePath(NewProgram, OldProgram) then
  begin
    if FileExists(AddBackslash(NewProgram) + 'launcher-licenses') then
    begin
      Result := FmtMessage(CustomMessage('SourceCollision'), ['launcher-licenses']);
      Exit;
    end;
    for I := 0 to 5 do
    begin
      Name := PayloadName(I);
      Target := AddBackslash(NewProgram) + Name;
      if FileExists(Target) or DirExists(Target) then
      begin
        Result := FmtMessage(CustomMessage('SourceCollision'), [Name]);
        Exit;
      end;
    end;
  end;
end;

function NextButtonClick(PageID: Integer): Boolean;
var
  Problem: String;
begin
  Result := True;
  if (PageID <> ComponentPage.ID) and ((PageID <> wpSelectDir) or (OldComponent = '')) then
    Exit;
  NewProgram := WizardDirValue;
  NewComponent := Trim(ComponentPage.Values[0]);
  Problem := DirectoryError;
  Result := Problem = '';
  if not Result then
    SuppressibleMsgBox(Problem, mbError, MB_OK, IDOK);
end;

procedure RemoveCachedInstaller(const Filename: String);
var
  Name: String;
begin
  if not SamePath(ExtractFileDir(Filename), CacheDirectory) or not FileExists(Filename) then
    Exit;
  Name := CachePrefix + GetSHA256OfFile(Filename) + '.exe';
  if CompareText(ExtractFileName(Filename), Name) = 0 then
    DeleteFile(Filename);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Probe, SourceInstaller, SourceHash: String;
begin
  NewProgram := ExpandConstant('{app}');
  NewComponent := Trim(ComponentPage.Values[0]);
  Result := DirectoryError;
  if Result <> '' then
    Exit;
  NewComponent := ExpandFileName(NewComponent);
  if OldComponent = '' then
  begin
    Result := CustomMessage('BadComponentDirectory');
    if not ForceDirectories(NewComponent) then
      Exit;
    Probe := CreateTempFile(NewComponent);
    if Probe = '' then
      Exit;
    DeleteFile(Probe);
  end;

  Result := CustomMessage('CacheError');
  if not ForceDirectories(CacheDirectory) then
    Exit;
  SourceInstaller := ExpandConstant('{srcexe}');
  SourceHash := GetSHA256OfFile(SourceInstaller);
  NewInstaller := AddBackslash(CacheDirectory) + CachePrefix + SourceHash + '.exe';
  InstallNeedsCache := not FileExists(NewInstaller);
  if not InstallNeedsCache and not SamePath(SourceInstaller, NewInstaller) then
    if GetSHA256OfFile(NewInstaller) <> SourceHash then
      Exit;

  NewArchives := ExpandConstant('{src}');
  if (Relocating or SamePath(SourceInstaller, OldInstaller)) and (OldArchives <> '') then
    NewArchives := OldArchives;
  Result := CustomMessage('LayoutError');
  PendingLayout := WriteLayoutTemp(UTF8Encode('{' + #13#10 +
    '  "program_dir": ' + EncodeJsonPath(NewProgram) + ',' + #13#10 +
    '  "component_dir": ' + EncodeJsonPath(NewComponent) + ',' + #13#10 +
    '  "archive_dir": ' + EncodeJsonPath(NewArchives) + ',' + #13#10 +
    '  "installer": ' + EncodeJsonPath(NewInstaller) + #13#10 + '}' + #13#10));
  if PendingLayout <> '' then
    Result := '';
end;

procedure RestoreLayout;
var
  Backup: String;
begin
  if OldLayout = '' then
    LayoutReplaced := not DeleteFile(LayoutPath)
  else
  begin
    Backup := WriteLayoutTemp(OldLayout);
    LayoutReplaced := (Backup = '') or not MoveFileEx(Backup, LayoutPath, 9);
    if Backup <> '' then
      DeleteFile(Backup);
  end;
  if LayoutReplaced then
    Log('Failed to restore the previous installation settings.');
end;

procedure VerifyInstallation;
var
  ExitCode: Integer;
begin
  if not MoveFileEx(PendingLayout, LayoutPath, 9) then
    RaiseException(CustomMessage('LayoutError'));
  LayoutReplaced := True;
  if not Exec(AddBackslash(NewProgram) + 'Q-Pixelate.exe', '--verify-installation', NewProgram,
      SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
  begin
    RestoreLayout;
    RaiseException(CustomMessage('VerifyError'));
  end;
end;

procedure RemovePreviousFiles;
var
  I: Integer;
  PreviousFile, CurrentFile: String;
begin
  for I := 0 to 5 do
  begin
    PreviousFile := AddBackslash(OldProgram) + PayloadName(I);
    CurrentFile := AddBackslash(NewProgram) + PayloadName(I);
    if FileExists(PreviousFile) and FileExists(CurrentFile) then
      if GetSHA256OfFile(PreviousFile) = GetSHA256OfFile(CurrentFile) then
        DeleteFile(PreviousFile);
  end;
  RemoveDir(AddBackslash(OldProgram) + 'launcher-licenses');
  RemoveDir(OldProgram);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    InstallCommitted := True;
  if CurStep <> ssDone then
    Exit;
  if not SamePath(OldInstaller, NewInstaller) then
    RemoveCachedInstaller(OldInstaller);
  if (OldProgram = '') or SamePath(OldProgram, NewProgram) then
    Exit;
  if SamePath(ExtractFileDir(OldUninstaller), OldProgram) and FileExists(OldUninstaller) then
  begin
    DeleteFile(OldUninstaller);
    DeleteFile(ChangeFileExt(OldUninstaller, '.dat'));
    DeleteFile(ChangeFileExt(OldUninstaller, '.msg'));
  end;
  if (ExpandConstant('{param:removeprevious|0}') = '1') or
    (not WizardSilent and
      (SuppressibleMsgBox(FmtMessage(CustomMessage('CleanupPrevious'), [OldProgram]),
       mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES)) then
    RemovePreviousFiles;
end;

procedure DeinitializeSetup;
begin
  if not InstallCommitted then
  begin
    if LayoutReplaced then
      RestoreLayout;
    if CacheCreated and not LayoutReplaced then
      RemoveCachedInstaller(NewInstaller);
  end;
  if PendingLayout <> '' then
    DeleteFile(PendingLayout);
end;

function InitializeUninstall: Boolean;
begin
  Result := SamePath(ExpandConstant('{app}'), ReadUninstallValue('Inno Setup: App Path'));
  if not Result then
    SuppressibleMsgBox(CustomMessage('InactiveUninstaller'), mbError, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Layout: AnsiString;
  ProgramDirectory: String;
  ExitCode: Integer;
begin
  if not LoadStringFromFile(LayoutPath, Layout) then
    Exit;
  ProgramDirectory := ExpandConstant('{app}');
  if not SamePath(ReadLayoutField(Layout, 'program_dir'), ProgramDirectory) then
    Exit;
  case CurUninstallStep of
    usUninstall:
      if (ExpandConstant('{param:removecomponents|0}') = '1') or
        (not UninstallSilent and
          (SuppressibleMsgBox(CustomMessage('RemoveComponents'), mbConfirmation,
           MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES)) then
        if not Exec(AddBackslash(ProgramDirectory) + 'Q-Pixelate.exe', '--remove-installed-components',
          ProgramDirectory, SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
        begin
          SuppressibleMsgBox(CustomMessage('ComponentRemovalError'), mbError, MB_OK, IDOK);
          Abort;
        end;
    usPostUninstall:
      begin
        RemoveCachedInstaller(ReadLayoutField(Layout, 'installer'));
        RemoveDir(CacheDirectory);
        DeleteFile(LayoutPath);
        RemoveDir(StateDirectory);
      end;
  end;
end;
