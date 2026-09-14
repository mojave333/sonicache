; Inno Setup script: wraps the PyInstaller build into a Windows installer.
;
;     powershell -File scripts\build-installer.ps1
;
; It expects the folder PyInstaller leaves in dist\Trackhound, so build that
; first: pyinstaller --noconfirm trackhound.spec
;
; The installer asks for no administrator rights. It installs into
; %LOCALAPPDATA%\Programs\Trackhound for the person running it, and only
; offers the machine-wide Program Files when that person is an administrator
; anyway — an unsigned installer that opens a UAC prompt saying "unknown
; publisher" is exactly what the README asks people not to be scared by, so
; the default avoids the prompt entirely.

#define AppName "Trackhound"
#define AppPublisher "mojave333"
#define AppUrl "https://github.com/mojave333/trackhound"

#ifndef SourceFolder
  #define SourceFolder "dist\Trackhound"
#endif
#define AppExe SourceFolder + "\Trackhound.exe"

#if !FileExists(AppExe)
  #error Build the program first: pyinstaller --noconfirm trackhound.spec
#endif

; build-installer.ps1 passes the version from trackhound/__init__.py, which is
; the single source of truth. On its own the script falls back to the resource
; the spec file stamps into the exe, which says the same thing with a ".0" on
; the end.
#ifndef AppVersion
  #define AppVersion GetVersionNumbersString(AppExe)
#endif

[Setup]
; Never change this: Windows recognises an installed copy by it, and a new one
; would turn every update into a second entry in the list of programs.
AppId={{FA88A291-13E9-40D1-9496-B10A55139A16}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases/latest
; The same reasoning as the version resource in trackhound.spec: an unsigned
; installer with no metadata at all is what the scanners like least, and
; filling this in costs nothing.
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppName}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} {#AppVersion} setup
VersionInfoCopyright=GPL-2.0-or-later
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\Trackhound.exe
UninstallDisplayName={#AppName}
LicenseFile=LICENSE
OutputDir=dist
OutputBaseFilename={#AppName}-{#AppVersion}-windows-x64-setup
SetupIconFile=trackhound\web\icon.ico
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; The program is 64-bit. "x64compatible" rather than "x64os" so that it also
; installs on arm64, where Windows runs it under x64 emulation. The identifier
; needs Inno Setup 6.3 or newer.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
; Offers to close a running copy instead of failing on a locked exe
CloseApplications=yes
RestartApplications=no
; Makes Setup tell the rest of Windows that PATH changed
ChangesEnvironment=yes
DisableProgramGroupPage=yes
ShowLanguageDialog=auto

[Languages]
; "auto" asks which language to use only when Windows is set to neither
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

; This file is saved as UTF-8 with a byte order mark on purpose: without the
; mark Inno Setup reads the script as ANSI and the Russian below turns to
; mojibake in the wizard.
[CustomMessages]
russian.AddToPath=Добавить Trackhound-cli в переменную PATH
russian.AddToPathInfo=Дополнительно:
russian.RemoveSettings=Удалить настройки Trackhound и журнал работы?%n%nСкачанная музыка не будет затронута — она останется в папке, которую вы выбрали в программе.
english.AddToPath=Add Trackhound-cli to the PATH variable
english.AddToPathInfo=Additional options:
english.RemoveSettings=Delete the Trackhound settings and log file?%n%nDownloaded music is left alone: it stays in the folder you chose in the program.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "addtopath"; Description: "{cm:AddToPath}"; GroupDescription: "{cm:AddToPathInfo}"; Flags: unchecked

[Files]
Source: "{#SourceFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The program is under the GPL, which asks for the licence to travel with it
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Trackhound.exe"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Trackhound.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "{code:EnvironmentKey}"; ValueType: expandsz; ValueName: "Path"; \
    ValueData: "{code:PathWithApp}"; Tasks: addtopath; Check: NotAlreadyOnPath

[Run]
Filename: "{app}\Trackhound.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[Code]
{ Where PATH lives depends on who the program was installed for. }
function EnvironmentKey(Param: string): string;
begin
  if IsAdminInstallMode then
    Result := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
  else
    Result := 'Environment';
end;

function CurrentPath: string;
begin
  if not RegQueryStringValue(HKEY_AUTO, EnvironmentKey(''), 'Path', Result) then
    Result := '';
end;

{ Matching on ";value;" keeps a folder whose name merely ends the same way
  (say ...\Trackhound-dev) from passing for one that is already listed. }
function IsListed(const Path, Folder: string): Boolean;
begin
  Result := Pos(';' + Uppercase(Folder) + ';', ';' + Uppercase(Path) + ';') > 0;
end;

function NotAlreadyOnPath: Boolean;
begin
  Result := not IsListed(CurrentPath, ExpandConstant('{app}'));
end;

{ A fresh user profile has no Path value at all, so the separator is only
  written when there is something to separate from. }
function PathWithApp(Param: string): string;
begin
  Result := CurrentPath;
  if Result <> '' then
    Result := Result + ';';
  Result := Result + ExpandConstant('{app}');
end;

procedure RemoveFromPath;
var
  Path, Folder: string;
  Position: Integer;
begin
  Folder := ExpandConstant('{app}');
  Path := CurrentPath;
  if not IsListed(Path, Folder) then
    Exit;
  { Cut the folder out together with one separator, then tidy up the ends the
    cut can leave behind. }
  Position := Pos(';' + Uppercase(Folder) + ';', ';' + Uppercase(Path) + ';');
  Delete(Path, Position, Length(Folder) + 1);
  if Copy(Path, 1, 1) = ';' then
    Delete(Path, 1, 1);
  if Copy(Path, Length(Path), 1) = ';' then
    Delete(Path, Length(Path), 1);
  RegWriteExpandStringValue(HKEY_AUTO, EnvironmentKey(''), 'Path', Path);
end;

procedure RemoveSettings;
begin
  DeleteFile(ExpandConstant('{%USERPROFILE}\.trackhound.json'));
  DelTree(ExpandConstant('{localappdata}\Trackhound'), True, True, True);
end;

procedure CurUninstallStepChanged(CurStep: TUninstallStep);
begin
  if CurStep <> usUninstall then
    Exit;
  RemoveFromPath;
  { Suppressible so that an unattended uninstall answers "no" and moves on
    instead of waiting for a click nobody is there to give. }
  if SuppressibleMsgBox(CustomMessage('RemoveSettings'), mbConfirmation, MB_YESNO, IDNO) = IDYES then
    RemoveSettings;
end;
