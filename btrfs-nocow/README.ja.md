# btrfs-nocow

btrfs で CoW（コピーオンライト）を切るべき場所を洗い出し、サブボリューム化・NOCOW 化して移し替えるツール。
VM イメージや DB のように、大きなファイルの中をランダムに上書きするデータが対象。

## なぜ必要か

- CoW のまま DB や VM イメージを上書きし続けると、ひどく断片化する。例えば、頻繁に更新される 322MB の SQLite が 43,691 エクステントになっていた。
- snapper などでスナップショットを取っている場所では、**NOCOW（`chattr +C`）にしても、スナップショット後の最初の書き込みで一度 CoW される**。1時間ごとのスナップショットの中では、NOCOW にしてもほとんど効かない。
- だから DB・VM・Docker のデータは、**別のサブボリューム（スナップショット対象外）にしたうえで NOCOW にする**。
- `chattr +C` は空のファイルにしか効かない。既存のデータは、作り直したディレクトリへコピーし直す必要がある。このツールはその作業を、サービスの停止から検証まで行う。
- 書いたら読むだけのデータ（モデル、ISO、コンテナのイメージ層）は、チェックサム（破損の検出）を残すため CoW のままにする。

## 必要なもの

- uv（スクリプトの依存の rich を自動で入れる）、Python 3.11 以上
- btrfs-progs、coreutils、e2fsprogs（`chattr`・`lsattr`・`filefrag`）、systemd
- 任意: docker（コンテナの再起動）、virsh（libvirt のプールの洗い出し）、snapper（スナップショット設定の読み取り）

パスの通った場所にリンクしておくと楽:

```sh
ln -s ~/ghq/github.com/akivajp/misc-scripts/btrfs-nocow/btrfs-nocow ~/.local/bin/
```

## 使い方

```sh
btrfs-nocow scan                 # 洗い出し（root なしでも動く。--sudo で root しか読めない場所も見る）
btrfs-nocow migrate --preset docker --dry-run   # 計画だけ表示（何も変更しない）
btrfs-nocow migrate --preset docker             # 移行（Docker を止めて、終わったら起動し直す）
btrfs-nocow cleanup              # 動作を確認してから、退避した <パス>.old を削除
btrfs-nocow rollback             # 元に戻す（移行の後に書き込まれたデータは失われる）
```

- `migrate`・`rollback`・`cleanup` は、一般ユーザーで実行すると sudo で自分を起動し直す（root の環境に uv や rich は要らない）。
- 自分のホームの中のディレクトリなら、`--no-sudo` で一般ユーザーのまま実行できる。ただし、そこに書き込むプロセスは先に止めておく。

### プリセット

| 名前 | 対象 | 止めるサービス |
|---|---|---|
| `docker` | `/var/lib/docker`（`volumes/` だけ NOCOW）、`/var/lib/containerd` | docker.socket・docker.service・containerd.service |
| `podman` | `/var/lib/containers`（`storage/volumes/` だけ NOCOW） | podman.socket・podman.service |
| `libvirt` | `/var/lib/libvirt/images`（全体を NOCOW）。VM は先に止めておく | なし |
| `mysql` | `/var/lib/mysql`（全体を NOCOW） | mysql.service・mariadb.service |
| `postgresql` | `/var/lib/postgresql`（全体を NOCOW） | postgresql.service |
| `mongodb` | `/var/lib/mongodb`（全体を NOCOW） | mongod.service |
| `atuin` | atuin（シェル履歴）のデータ（全体を NOCOW、サブボリュームにはしない） | atuin のデーモン（プロセス） |

- `atuin` 以外は、どれもサブボリュームとして作り直す。
- 止めるのは動いているサービスだけで、終わったら逆順に起動し直す。
- `docker` では、移行前に動いていたコンテナのうち、再起動ポリシーで上がらなかったものも起動し直す。
- `atuin` は一般ユーザーのまま動く（sudo で起動し直さない）。
  - 置き場所は `atuin info` の `client db path` から調べる。版や `ATUIN_HOME` で、`~/.atuin/data` や `~/.local/share/atuin` に変わるため。
  - デーモンは、コマンドライン・環境変数・作業ディレクトリを控えてから止め、終わったら同じ形で起動し直す。
  - 履歴をスナップショットで守るため、サブボリュームにはしない。スナップショットの後の書き込みは一度 CoW されるが、コピーし直すことで、それまでの断片化は解消する。
  - 戻す・消すときは `btrfs-nocow rollback --no-sudo` / `btrfs-nocow cleanup --no-sudo`。一般ユーザーの記録は `~/.local/state/btrfs-nocow/` にあるため。

### 任意のディレクトリ

```sh
btrfs-nocow migrate /srv/vm --subvolume --nocow                          # サブボリュームにして全体を NOCOW
btrfs-nocow migrate /opt/app/data --subvolume --nocow-subdir db --stop app.service   # db/ だけ NOCOW
btrfs-nocow migrate ~/data --nocow --no-sudo                             # 自分のディレクトリ（書き込むプロセスは先に止める）
```

## migrate がすること

1. **事前確認**（何も変えない）。次のどれかに当たれば止める。
   - btrfs の上ではない
   - マウントポイントである
   - 入れ子のサブボリュームがある（コピーすると普通のディレクトリになってしまう）
   - 読めないファイルがある
   - `<パス>.old` が既にある
   - 実コピーの分の空き容量が無い

   計画の表を出して確認を求める（`--yes` で省略）。
2. サービスやプロセスを止める。そのうえで、対象のファイルを開いているプロセス（cwd・mmap を含む）や、中のマウントが残っていないかを確かめる。残っていれば止める。
3. 元を `<パス>.old` に rename し、新しいサブボリューム（またはディレクトリ）を作る。所有者・権限・拡張属性（ACL・SELinux）を写し、NOCOW にする範囲へ先に `chattr +C` を付ける。
4. コピーする。
   - CoW のままの部分は `cp -a --reflink=auto`。同じファイルシステムなら、サブボリュームをまたいでもデータを共有するので一瞬で終わり、容量も増えない。
   - NOCOW の部分は `cp -a --reflink=never` で実際にコピーする。書き込み量で進捗を出す。
5. 検証する。
   - 元と新しい側で、ファイル数・容量が一致すること
   - NOCOW の範囲の全ファイルに C 属性が付いていること
   - サブボリュームになっていること
   - コピーの間に対象を開いたプロセスがいないこと（シェルのフック等で自動で起動し直したプロセスなど）
6. サービス・コンテナ・プロセスを起動し直す。

途中で失敗したら（Ctrl+C を含む）、新しい側を消して `<パス>.old` を戻し、サービスを起動し直す。
移行の記録は root なら `/var/lib/btrfs-nocow/state.json`、一般ユーザーなら `~/.local/state/btrfs-nocow/state.json` に残る。`rollback` と `cleanup` はこれを参照する。

ログは呼び出したユーザーの `~/logs/btrfs-nocow/<日時>-<サブコマンド>.log` に書く（`--log` で変えられる）。時間のかかる移行は、`tail -f` で経過を追える。

## 注意

- NOCOW のファイルには**データのチェックサムが付かない**。scrub で破損を検出できず、RAID1 でも自己修復されない。圧縮も効かない。
- `cleanup` の後も、古いスナップショットが参照している分は、保持期限が切れるまで容量が戻らない。
- `migrate` はディレクトリごと作り直すので、同じ場所にある他のデータもまとめて移る。
- swap ファイルは扱わない。`btrfs filesystem mkswapfile` で、専用のサブボリュームに作り直す。
- timeshift や btrbk のスナップショットは、scan の判定に入らない（設定ファイルがあれば警告だけ出す）。

## テスト

```sh
uv run --with pytest --with rich pytest btrfs-nocow/tests
```

結合テストは、btrfs の上の一時ディレクトリで root なしに行う。サブボリュームの作成と `chattr +C` は、自分のディレクトリなら一般ユーザーでもできる。`/tmp` が btrfs でない機械では、`BTRFS_NOCOW_TEST_DIR` に btrfs 上のディレクトリを指定する。
