<#
.SYNOPSIS
    Windows 11 検証用仮想マシンの初期設定（無人インストールの specialize の段階で、SYSTEM 権限で実行される）。

.DESCRIPTION
    - SSH サーバー（Win32-OpenSSH）を、同梱の MSI から導入する（ゲストからインターネットに出ない）
    - 管理者の公開鍵を登録し、ファイル転送（sftp）のプログラムを絶対パスで指定する
    - ファイアウォールで SSH と ping を許可する
    - スリープ・画面の消灯・ロック画面・初回サインインのアニメーションを止める
    - デバイスの暗号化の自動有効化と、ログオン中の自動再起動を防ぐ
    ログは C:\setup\specialize.log に残る。

.PARAMETER SourceDrive
    設定用の ISO のドライブ（例: E:）。setup フォルダーの中身を読む。
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceDrive
)

$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force -Path 'C:\setup' | Out-Null
Start-Transcript -Path 'C:\setup\specialize.log' -Append | Out-Null

$src = Join-Path $SourceDrive 'setup'
Write-Output "設定用の ISO: $src"

# --- 1. SSH サーバーを MSI から導入する（Server の機能だけ） ---
$msi = Join-Path $src 'OpenSSH-Win64.msi'
$msiArgs = @('/i', "`"$msi`"", 'ADDLOCAL=Server', '/qn', '/norestart', '/l*v', 'C:\setup\openssh-msi.log')
$proc = Start-Process -FilePath 'msiexec.exe' -ArgumentList $msiArgs -Wait -PassThru
Write-Output "OpenSSH の MSI の終了コード: $($proc.ExitCode)"

# SSH の既定のシェル（DefaultShell）は設定しない（cmd.exe のまま）。
# PowerShell にすると、sftp のプログラムも PowerShell 経由で起動され、引用符付きのパスが文字列として出力されて
# 転送が壊れる（scp・sftp が失敗する）。PowerShell を使うときは、コマンド側で powershell.exe を明示する

# --- 2. sshd を一度起動して C:\ProgramData\ssh（設定・ホスト鍵）を作らせる ---
Set-Service -Name 'sshd' -StartupType Automatic
Start-Service -Name 'sshd'
$sshDir = 'C:\ProgramData\ssh'
if (-not (Test-Path $sshDir)) {
    New-Item -ItemType Directory -Force -Path $sshDir | Out-Null
}

# --- 3. sftp のプログラムを絶対パスで指定する ---
# MSI で Server の機能だけを入れると、インストール先が PATH に入らず、
# 既定の「Subsystem sftp sftp-server.exe」ではプログラムが見つからない（scp・sftp が Connection closed で失敗する）
$sshdConfig = Join-Path $sshDir 'sshd_config'
if (Test-Path $sshdConfig) {
    $sftpServer = Join-Path $env:ProgramFiles 'OpenSSH\sftp-server.exe'
    (Get-Content $sshdConfig) -replace '^Subsystem\s+sftp\s+.*$', "Subsystem`tsftp`t`"$sftpServer`"" |
        Set-Content -Encoding ascii $sshdConfig
    Write-Output "sshd_config の sftp: $sftpServer"
} else {
    Write-Output "警告: $sshdConfig がありません（sftp の設定を直せていない）"
}

# --- 4. 管理者の公開鍵を登録する（管理者と SYSTEM だけが読める権限にしないと sshd が無視する） ---
$authorizedKeys = Join-Path $sshDir 'administrators_authorized_keys'
Copy-Item -Path (Join-Path $src 'authorized_keys') -Destination $authorizedKeys -Force
# 日本語版でも名前に依存しないよう、SID で指定する（S-1-5-32-544: Administrators、S-1-5-18: SYSTEM）
icacls.exe $authorizedKeys /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null
Restart-Service -Name 'sshd'

# --- 5. ファイアウォール: SSH（22 番）と ping を、すべてのプロファイルで許可する ---
netsh.exe advfirewall firewall add rule name='win11vm: OpenSSH Server' dir=in action=allow protocol=TCP localport=22 profile=any | Out-Null
netsh.exe advfirewall firewall add rule name='win11vm: ICMPv4 echo' dir=in action=allow 'protocol=icmpv4:8,any' profile=any | Out-Null

# --- 6. 電源: スリープ・画面の消灯・休止状態を止める ---
powercfg.exe /change monitor-timeout-ac 0
powercfg.exe /change standby-timeout-ac 0
powercfg.exe /hibernate off

# --- 7. レジストリ ---
$regs = @(
    # 自動ログオンを妨げる「パスワードレスのサインインを必須にする」を切る
    @('HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device', 'DevicePasswordLessBuildVersion', 0),
    # 初回サインインのアニメーションを出さない
    @('HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System', 'EnableFirstLogonAnimation', 0),
    # SSH などのネットワーク経由のログオンでも、管理者の権限をそのまま使えるようにする（検証用の仮想マシンに限る）
    @('HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System', 'LocalAccountTokenFilterPolicy', 1),
    # デバイスの暗号化（BitLocker）を自動で有効にしない。ディスクを初期状態に戻して使い回すため
    @('HKLM\SYSTEM\CurrentControlSet\Control\BitLocker', 'PreventDeviceEncryption', 1),
    # ログオン中は、Windows Update による自動再起動をしない
    @('HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU', 'NoAutoRebootWithLoggedOnUsers', 1),
    # ロック画面を出さない
    @('HKLM\SOFTWARE\Policies\Microsoft\Windows\Personalization', 'NoLockScreen', 1)
)
foreach ($r in $regs) {
    reg.exe add $r[0] /v $r[1] /t REG_DWORD /d $r[2] /f | Out-Null
    Write-Output "レジストリ: $($r[0]) $($r[1]) = $($r[2])"
}

Write-Output '初期設定が終わりました'
Stop-Transcript | Out-Null
