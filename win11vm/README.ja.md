# win11vm

Windows 11 評価版（Enterprise、90日）の検証用仮想マシンを libvirt / KVM 上に無人で作り、ホストから操作するためのスクリプト。
1台を複数のプロジェクトで使い回す前提で、使い終わったらディスクを初期状態に戻せる。

## 必要なもの

- libvirt（`qemu:///system`）、QEMU、virt-install、OVMF（セキュアブート用）、swtpm、xorriso
- 実行するユーザーが `libvirt` グループに入っていること（sudo は要らない）
- Python 3（画面の撮影結果を PNG にするのに使う。標準ライブラリだけ）

## 作る

```sh
./win11vm download              # 評価版の ISO（約 8GB）と SSH サーバーの MSI を取得し、SHA-256 を照合する
./win11vm create                # 仮想マシンを作り、無人インストールを始める
./win11vm ssh hostname          # SSH が通れば、インストールは完了
./win11vm stop && ./win11vm save-clean   # ISO を外して消し、初期状態として保存する
```

所要時間の目安（2026-11-01、Ryzen 9 9950X3D・NVMe で計測）: `create` から SSH が通るまで約6分半、自動ログオンでデスクトップが出るまで約8分。
評価版のライセンスは、ログオンの数分後にインターネット経由で自動的に認証され、90 日の期間が始まる（それまでは右下に「有効期限が切れています」と出る）。

## 使う

```sh
./win11vm status                                   # 状態・IP・使用中の印を見る
./win11vm use myproj                               # 使用中の印を付ける（ほかの作業が使用中なら止まる）
./win11vm start
./win11vm push build/app.exe C:/work/myproj        # ファイルを送る
./win11vm run-gui C:/work/myproj/app.exe           # ログオン中の画面で起動する
./win11vm run-gui --hidden powershell.exe -File C:/work/myproj/check.ps1   # ウィンドウを出さずに動かす
./win11vm key KEY_VOLUMEUP                         # キーを送る（Linux のキー名）
./win11vm shot after-volume-up                     # 画面を PNG で保存する
./win11vm ps 'Get-Process app'                     # PowerShell を実行する
./win11vm pull C:/work/myproj/result.txt .         # ファイルを取り出す
./win11vm stop && ./win11vm reset && ./win11vm release   # 初期状態に戻して、使用中の印を外す
./win11vm license                                  # 評価版の残り日数
```

サブコマンドの一覧は `./win11vm --help`。`ssh` の既定のシェルは cmd.exe なので、PowerShell は `ps` を使う。

## ファイルの構成

| パス | 内容 |
|---|---|
| `win11vm` | 本体 |
| `ppm2png.py` | `virsh screenshot` の PPM を PNG にする |
| `unattend/autounattend.xml.in` | 無人インストールの設定。`create` が `@...@` を置き換えて設定用の ISO を作る |
| `unattend/setup/specialize.ps1` | インストール中に SYSTEM 権限で動く初期設定（SSH サーバーの導入、公開鍵の登録、電源・ロック画面の設定など） |

状態は `~/vms/win11-eval/`（`WIN11VM_HOME` で変えられる）に置き、git には入れない。

| パス | 内容 |
|---|---|
| `iso/` | 評価版の ISO（`save-clean` の後は消してよい。作り直すときは取り直す） |
| `cache/` | SSH サーバーの MSI、設定用の ISO（パスワードを含む） |
| `secrets/` | ログオンのパスワード（`vm.env`）、SSH の鍵、known_hosts |
| `logs/` | `download`・`create`・`save-clean` などのログ |
| `shots/` | 画面の撮影結果 |
| `in-use` | 使用中の印（`use` が作り、`release` が消す） |

ディスクと ISO の実体は、libvirt のプール（既定は `vm-images` = `/srv/libvirt/images`）に置く。
qemu がホームディレクトリを読めなくても動くように、ISO はプールへアップロードしてから使う。

プールは、無ければ `create` が作る。btrfs の場合に備えて、次の2点に気を付けている。

- **CoW を切る**: ディスクイメージに CoW が効くと、書き込みのたびに断片化して遅くなる。プールの定義に `<cow state='no'/>` を付けて `pool-build` すると、root で動く libvirt がディレクトリに `chattr +C` 相当の属性を付け、中に作るファイルにも引き継がれる（sudo は要らない）。この属性は空のファイルにしか効かないので、ディレクトリに付けておく。
- **スナップショットの対象外に置く**: snapper などでスナップショットを取っている subvolume（例: `/` や `/home`）に置くと、CoW を切っていても、スナップショットの後の最初の書き込みでコピーが起き、スナップショットも膨らむ。対象外の subvolume（例: `/srv`）を `WIN11VM_POOL_DIR` で指定する。

## 仮想マシンの構成

- q35、UEFI（セキュアブート。Microsoft の 2011 年と 2023 年の鍵を登録済み）、TPM 2.0（swtpm）
- メモリ 8GB、CPU 4、ディスク 80GB（qcow2。インストール直後で約 16GB）、SATA
- ネットワーク: libvirt の `default`（NAT。ホストからだけつながる）、e1000e
- 音声: ich9（HD Audio）。音声の装置が無いと、音量の操作や表示を確かめられない
- 画面: VGA（1280×800）。VNC は 127.0.0.1 だけで待ち受ける
- 日本語、日本語キーボード、東京の時間帯。ローカルの管理者 `tester` で自動ログオンする

## 確かめられること・確かめられないこと

確かめられる（2026-11-01 に確認したものを含む）:

- 画面の表示（位置・大きさ）、キー入力に対する反応
- 音量キー・メディアキー（`key KEY_VOLUMEUP` で、Windows 標準の音量表示が出ることを確認済み。PS/2 キーボードとして届く）
- アプリの起動・終了、フォーカス、CPU やメモリの使用量、異常終了した後の状態
- ログオン中の画面のウィンドウの構成（`run-gui --hidden` で調べるスクリプトを動かし、結果を `pull` する）

確かめられない（実機で確かめる）:

- 画面の明るさ・機内モード（仮想マシンには明るさの調整も無線も無い）
- 実際のキーボードやマウスの特殊なボタン（HID の Consumer の報告で届くもの）、Bluetooth の機器
- 全画面のゲーム、GPU を使う描画、手元の複数モニターの構成

## 90 日の期限が来たら

評価版は 90 日で期限が切れる（壁紙が黒くなり、1時間ごとに電源が切れる）。作り直す:

```sh
./win11vm destroy --yes
./win11vm download    # 新しい版に変わっていたら、win11vm の ISO_URL・ISO_FILE・ISO_SHA256 を更新する
./win11vm create
./win11vm stop && ./win11vm save-clean
```

## 仕組みのメモ

- **CD から起動するときのキー**: UEFI で Windows の ISO から起動すると「Press any key to boot from CD or DVD」が数秒だけ出る。`create` はその間、下矢印キーを送り続ける。スペースや Enter だと、送っている間にセットアップの画面が出たとき、フォーカスのある「キャンセル」ボタンを押してしまう。起動の順番をディスク → ISO にしているので、インストール後はディスクから起動し、この表示は二度と出ない。
- **PowerShell の文字コード**: Windows PowerShell 5.1 は BOM の無いスクリプトを既定のコードページ（日本語版では CP932）で読み、日本語のコメントで構文が壊れる。設定用の ISO を作るとき、UTF-8 の BOM を付けて入れる。
- **SSH の公開鍵**: 管理者の鍵は `C:\ProgramData\ssh\administrators_authorized_keys` に置き、管理者と SYSTEM だけが読める権限にする（そうしないと sshd が無視する）。日本語版でも名前に依存しないよう、SID で権限を付ける。
- **sftp（scp）のプログラムは絶対パスで指定する**: MSI で Server の機能だけを入れると、インストール先が PATH に入らず、既定の `Subsystem sftp sftp-server.exe` では見つからない（scp が `Connection closed` で失敗する）。
- **SSH の既定のシェルは変えない**: PowerShell にすると、sftp のプログラムも PowerShell 経由で起動され、引用符付きのパスが文字列として出力されて転送が壊れる（`Ensure the remote shell produces no output` で失敗する）。PowerShell は `ps` で明示して呼ぶ。
- **画面に出るプログラムの起動**: SSH のセッションは画面の無いセッションで動くので、`run-gui` はタスク スケジューラーに「ログオン中のユーザー」として登録して起動する。コンソールのプログラムは Windows Terminal のウィンドウで開き、`-WindowStyle Hidden` も効かないので、`--hidden` では `conhost.exe --headless` を前に付ける。
- **初期状態**: `save-clean` は ISO を外して消し、ディスクを `<名前>-clean.qcow2` に複製する。`reset` はそれで上書きする（16GB で約10秒）。デバイスの暗号化（BitLocker）は自動で有効にならないようにしてあるので、TPM の状態はそのままでよい。
- **停止**: `stop` は ACPI でシャットダウンし、「shut off」になるまで待つ（途中の「in shutdown」では `reset` できない）。
