"""btrfs-nocow のテスト。

実行: uv run --with pytest --with rich pytest btrfs-nocow/tests

結合テストは btrfs の上に一時ディレクトリを作って root なしで行う（サブボリュームの作成と chattr +C は
自分のディレクトリなら一般ユーザーでもできる）。既定の一時ディレクトリが btrfs でない機械では、
BTRFS_NOCOW_TEST_DIR に btrfs 上のディレクトリを指定する（指定が無ければ結合テストは飛ばす）。
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "btrfs-nocow"


def load_module() -> ModuleType:
    """拡張子の無いスクリプトをモジュールとして読み込む。"""
    loader = importlib.machinery.SourceFileLoader("btrfs_nocow", str(SCRIPT))
    spec = importlib.util.spec_from_loader("btrfs_nocow", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["btrfs_nocow"] = mod
    loader.exec_module(mod)
    return mod


bn = load_module()


# =====================================================================
# 単体テスト
# =====================================================================


def test_parse_size() -> None:
    assert bn.parse_size("32M") == 32 << 20
    assert bn.parse_size("1G") == 1 << 30
    assert bn.parse_size("512") == 512
    assert bn.parse_size("1.5KiB") == 1536


def test_parse_mountinfo_unescapes_and_reads_btrfs_root() -> None:
    text = (
        "29 1 0:26 /@ / rw,relatime shared:1 - btrfs /dev/nvme0n1p1 rw,ssd,subvol=/@\n"
        "30 29 0:26 /@home /home rw,relatime shared:2 - btrfs /dev/nvme0n1p1 rw,ssd,subvol=/@home\n"
        "31 29 0:40 / /mnt/my\\040disk rw - ext4 /dev/sda1 rw\n"
    )
    mounts = bn.parse_mountinfo(text)
    assert [m.mount_point for m in mounts] == ["/", "/home", "/mnt/my disk"]
    assert mounts[1].root == "/@home" and mounts[1].fstype == "btrfs"
    assert bn.mount_of(Path("/home/a/b"), mounts).mount_point == "/home"
    assert bn.mount_of(Path("/homework"), mounts).mount_point == "/"
    assert bn.mount_of(Path("/mnt/my disk/x"), mounts).fstype == "ext4"


def test_is_under() -> None:
    assert bn.is_under("/var/lib/docker/volumes", "/var/lib/docker")
    assert bn.is_under("/var/lib/docker", "/var/lib/docker/")
    assert not bn.is_under("/var/lib/docker2", "/var/lib/docker")
    assert bn.is_under("/anything", "/")


def test_parse_snapper_config() -> None:
    text = '# comment\nSUBVOLUME="/home"\nFSTYPE="btrfs"\nTIMELINE_CREATE="yes"\n'
    kv = bn.parse_snapper_config(text)
    assert kv["SUBVOLUME"] == "/home" and kv["TIMELINE_CREATE"] == "yes"


def _judge(**kw: object) -> object:
    base = dict(path="/var/lib/mysql", exists=True, want_subvolume=True, want_nocow=True, nocow_subdirs=(), is_subvol=False,
                in_snapshot=True, nocow=False, preset=None, size=None)
    base.update(kw)
    return bn.judge_location(**base)


def test_judge_location() -> None:
    # スナップショットの中で CoW のまま → 両方を直すコマンド
    v = _judge()
    assert v.status == "推奨" and "--subvolume" in v.command and "--nocow" in v.command
    # サブボリュームにして NOCOW 済み → 対応済み
    assert _judge(is_subvol=True, in_snapshot=False, nocow=True).status == "対応済み"
    # スナップショット対象外なら、サブボリュームでなくても NOCOW だけでよい
    assert _judge(in_snapshot=False, nocow=True).status == "対応済み"
    # NOCOW だがスナップショットの中 → サブボリューム化を勧める
    v = _judge(nocow=True)
    assert v.status == "推奨" and "スナップショット" in v.reason
    # プリセットがあればプリセットのコマンド
    assert _judge(preset="mysql").command == "btrfs-nocow migrate --preset mysql"
    # 属性が読めない・無い・ほぼ空
    assert _judge(nocow=None).status == "要 root"
    assert _judge(exists=False).status == "無し"
    assert _judge(size=1024).status == "不要"
    # NOCOW を求めない置き場所（containerd）は、属性が読めなくてもスナップショットだけで判定する
    assert _judge(want_nocow=False, nocow=None).status == "推奨"


def test_judge_file() -> None:
    mib = 1 << 20
    assert bn.judge_file(kind="DB", size=300 * mib, extents=40000, age_days=0.1, nocow=False, in_snapshot=True).status == "推奨"
    assert bn.judge_file(kind="DB", size=300 * mib, extents=3, age_days=0.1, nocow=False, in_snapshot=True).status == "不要"
    assert bn.judge_file(kind="VM", size=10 << 30, extents=5, age_days=0.1, nocow=False, in_snapshot=False).status == "推奨"
    assert bn.judge_file(kind="VM", size=10 << 30, extents=5, age_days=400, nocow=False, in_snapshot=True).status == "不要"
    assert bn.judge_file(kind="VM", size=10 << 30, extents=5, age_days=1, nocow=True, in_snapshot=False).status == "対応済み"
    assert bn.judge_file(kind="VM", size=10 << 30, extents=5, age_days=1, nocow=True, in_snapshot=True).status == "推奨"


def test_specs_from_args_validation() -> None:
    parser = bn.build_parser()
    with pytest.raises(bn.MigrationError):
        bn.specs_from_args(parser.parse_args(["migrate", "/x"]))  # 操作の指定が無い
    with pytest.raises(bn.MigrationError):
        bn.specs_from_args(parser.parse_args(["migrate", "/x", "--nocow", "--nocow-subdir", "a"]))
    with pytest.raises(bn.MigrationError):
        bn.specs_from_args(parser.parse_args(["migrate", "/x", "--nocow-subdir", "../a"]))
    with pytest.raises(bn.MigrationError):
        bn.specs_from_args(parser.parse_args(["migrate", "/x", "--preset", "docker"]))
    specs, preset = bn.specs_from_args(parser.parse_args(["migrate", "/x", "--subvolume", "--nocow-subdir", "a/b"]))
    assert preset is None and specs[0].subvolume and specs[0].nocow_subdirs == ("a/b",)


# =====================================================================
# 結合テスト（btrfs の上で、root なし）
# =====================================================================


def _btrfs_base() -> Path | None:
    """結合テストに使える btrfs 上のディレクトリ。"""
    cands = [os.environ.get("BTRFS_NOCOW_TEST_DIR"), tempfile.gettempdir()]
    for c in filter(None, cands):
        m = bn.mount_of(Path(c).resolve(), bn.read_mounts())
        if m and m.fstype == "btrfs":
            return Path(c).resolve()
    return None


@pytest.fixture
def work() -> Iterator[Path]:
    """btrfs の上の作業ディレクトリ（サブボリュームが残っても消せるよう後片付けする）。"""
    base = _btrfs_base()
    if base is None:
        pytest.skip("btrfs の一時ディレクトリが無い（BTRFS_NOCOW_TEST_DIR で指定する）")
    d = Path(tempfile.mkdtemp(prefix="btrfs-nocow-test-", dir=base))
    probe = d / "probe"
    probe.mkdir()
    if subprocess.run(["chattr", "+C", str(probe)], capture_output=True).returncode != 0:
        pytest.skip("chattr +C が使えない")
    probe.rmdir()
    yield d
    # 一般ユーザーが作ったサブボリュームは、中身を消してから rmdir で消せる
    subprocess.run(["rm", "-rf", "--one-file-system", str(d)], check=False)
    for p in sorted(d.rglob("*"), key=lambda x: -len(x.parts)) if d.exists() else []:
        subprocess.run(["rm", "-rf", "--one-file-system", str(p)], check=False)
        if p.is_dir():
            os.rmdir(p)
    if d.exists():
        os.rmdir(d)


def make_tree(root: Path) -> None:
    """いろいろな種類のエントリを持つディレクトリを作る。"""
    root.mkdir()
    (root / "image").mkdir()
    (root / "image" / "layer.bin").write_bytes(os.urandom(3 << 20))
    os.link(root / "image" / "layer.bin", root / "image" / "layer-hardlink.bin")
    (root / "image" / "link").symlink_to("layer.bin")
    (root / ".hidden").write_text("dot\n")
    (root / "empty").mkdir()
    (root / "storage" / "volumes" / "db").mkdir(parents=True)
    (root / "storage" / "volumes" / "db" / "ibdata1").write_bytes(os.urandom(2 << 20))
    (root / "storage" / "other.txt").write_text("other\n")
    os.setxattr(root / "storage" / "other.txt", "user.test", b"value")
    os.chmod(root, 0o711)


def snapshot_tree(root: Path) -> dict[str, tuple[str, str]]:
    """比較用に、相対パス → (種類と権限, 内容のハッシュかリンク先) を集める。"""
    out: dict[str, tuple[str, str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            s = os.lstat(p)
            rel = str(p.relative_to(root))
            if p.is_symlink():
                out[rel] = (f"l{oct(s.st_mode)}", os.readlink(p))
            elif p.is_dir():
                out[rel] = (f"d{oct(s.st_mode)}", "")
            else:
                out[rel] = (f"f{oct(s.st_mode)}", hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def cli(work: Path, *args: str) -> int:
    """CLI を確認なし・sudo なし・作業ディレクトリ内の記録とログで実行する。"""
    sub, *rest = args
    return bn.main(["--log", str(work / "test.log"), sub, *rest, "--yes", "--no-sudo", "--state", str(work / "state.json")])


def shared_extents(path: Path) -> bool:
    """filefrag -v で共有（reflink）されたエクステントがあるか。"""
    out = subprocess.run(["filefrag", "-v", str(path)], capture_output=True, text=True).stdout
    return "shared" in out


def test_migrate_subvolume_with_nocow_subdir_then_cleanup(work: Path) -> None:
    target = work / "docker"
    make_tree(target)
    before = snapshot_tree(target)
    mtime = os.lstat(target).st_mtime_ns

    assert cli(work, "migrate", str(target), "--subvolume", "--nocow-subdir", "storage/volumes") == 0

    assert bn.is_subvolume_root(target)
    assert snapshot_tree(target) == before  # 中身・権限・リンクが同じ
    assert oct(os.lstat(target).st_mode & 0o7777) == "0o711"
    assert os.lstat(target).st_mtime_ns == mtime
    assert os.stat(target / "image" / "layer.bin").st_ino == os.stat(target / "image" / "layer-hardlink.bin").st_ino
    assert os.getxattr(target / "storage" / "other.txt", "user.test") == b"value"
    # NOCOW は指定したサブディレクトリの中だけ
    assert bn.get_nocow(target / "storage" / "volumes") is True
    assert bn.get_nocow(target / "storage" / "volumes" / "db" / "ibdata1") is True
    assert bn.get_nocow(target / "storage") is False
    assert bn.get_nocow(target / "image" / "layer.bin") is False
    # CoW の側は reflink で共有し、NOCOW の側は実コピー
    assert shared_extents(target / "image" / "layer.bin")
    assert not shared_extents(target / "storage" / "volumes" / "db" / "ibdata1")
    assert (work / "docker.old").is_dir()

    assert cli(work, "cleanup") == 0
    assert not (work / "docker.old").exists()
    assert bn.State(work / "state.json").migrations == {}
    # 移行済みのものをもう一度指定すると何もしない
    assert cli(work, "migrate", str(target), "--subvolume", "--nocow-subdir", "storage/volumes") == 0
    assert not (work / "docker.old").exists()


def test_migrate_nocow_all_then_rollback(work: Path) -> None:
    target = work / "db"
    make_tree(target)
    before = snapshot_tree(target)
    ino = os.lstat(target).st_ino

    assert cli(work, "migrate", str(target), "--nocow") == 0
    assert not bn.is_subvolume_root(target)
    assert bn.walk_tree(target, check_nocow=True).not_nocow == 0
    assert snapshot_tree(target) == before

    assert cli(work, "rollback") == 0
    assert os.lstat(target).st_ino == ino  # 元のディレクトリそのもの
    assert snapshot_tree(target) == before
    assert not (work / "db.old").exists()


def test_rollback_removes_user_subvolume(work: Path) -> None:
    target = work / "vm"
    make_tree(target)
    before = snapshot_tree(target)
    assert cli(work, "migrate", str(target), "--subvolume", "--nocow") == 0
    assert bn.is_subvolume_root(target)
    assert cli(work, "rollback", str(target)) == 0
    assert not bn.is_subvolume_root(target)
    assert snapshot_tree(target) == before


def test_failure_restores_original(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = work / "data"
    make_tree(target)
    before = snapshot_tree(target)
    ino = os.lstat(target).st_ino

    def broken_verify(plan: object, mps: set[str]) -> None:
        raise bn.MigrationError("検証の失敗（テスト）")

    monkeypatch.setattr(bn, "verify", broken_verify)
    assert cli(work, "migrate", str(target), "--subvolume", "--nocow") == 1
    assert os.lstat(target).st_ino == ino
    assert snapshot_tree(target) == before
    assert not (work / "data.old").exists()
    assert bn.State(work / "state.json").migrations == {}


def test_refuses_nested_subvolume(work: Path) -> None:
    target = work / "outer"
    make_tree(target)
    subprocess.run(["btrfs", "subvolume", "create", str(target / "inner")], check=True, capture_output=True)
    assert cli(work, "migrate", str(target), "--nocow") == 1
    assert not (work / "outer.old").exists()


def test_refuses_when_in_use(work: Path) -> None:
    target = work / "busy"
    make_tree(target)
    proc = subprocess.Popen(["sleep", "30"], cwd=target)
    try:
        assert cli(work, "migrate", str(target), "--nocow") == 1
        assert not (work / "busy.old").exists()
    finally:
        proc.kill()
        proc.wait()


def test_dry_run_changes_nothing(work: Path) -> None:
    target = work / "dry"
    make_tree(target)
    before = snapshot_tree(target)
    ino = os.lstat(target).st_ino
    assert bn.main(["--log", str(work / "test.log"), "migrate", str(target), "--subvolume", "--nocow", "--dry-run", "--no-sudo",
                    "--state", str(work / "state.json")]) == 0
    assert os.lstat(target).st_ino == ino
    assert snapshot_tree(target) == before
    assert not (work / "dry.old").exists()


def test_units_restarted_when_stop_fails(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """2つ目のサービスが止まらなかったら、移行せずに、停止を送ったものを逆順に起動し直す。"""
    target = work / "svc"
    make_tree(target)
    active = {"a.service": True, "b.service": True}
    calls: list[list[str]] = []

    def fake_unit_state(unit: str) -> tuple[bool, bool]:
        return True, active[unit]

    def fake_run(cmd: list[str], *, check: bool = True, quiet: bool = False) -> object:
        calls.append(cmd)
        if cmd[:2] == ["systemctl", "stop"] and cmd[2] == "a.service":
            active["a.service"] = False  # a は止まる、b は止まらない
        if cmd[:2] == ["systemctl", "start"]:
            active[cmd[2]] = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bn, "unit_state", fake_unit_state)
    monkeypatch.setattr(bn, "run", fake_run)
    assert cli(work, "migrate", str(target), "--nocow", "--stop", "a.service", "--stop", "b.service") == 1
    starts = [c[2] for c in calls if c[:2] == ["systemctl", "start"]]
    assert starts == ["b.service", "a.service"]  # 止めた順の逆
    assert not (work / "svc.old").exists()


def test_units_stopped_together_are_restarted(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ソケットを止めるとサービスも一緒に止まる場合（docker.socket と docker.service）も、両方を起動し直す。"""
    target = work / "sock"
    make_tree(target)
    active = {"x.socket": True, "x.service": True}
    calls: list[list[str]] = []
    real_run = bn.run

    def fake_unit_state(unit: str) -> tuple[bool, bool]:
        return True, active[unit]

    def fake_run(cmd: list[str], *, check: bool = True, quiet: bool = False) -> object:
        if cmd[0] != "systemctl":  # chattr 等は本物を使う
            return real_run(cmd, check=check, quiet=quiet)
        calls.append(cmd)
        if cmd[:3] == ["systemctl", "stop", "x.socket"]:
            active["x.socket"] = active["x.service"] = False  # ソケットと一緒にサービスも止まる
        if cmd[:2] == ["systemctl", "start"]:
            active[cmd[2]] = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bn, "unit_state", fake_unit_state)
    monkeypatch.setattr(bn, "run", fake_run)
    assert cli(work, "migrate", str(target), "--nocow", "--stop", "x.socket", "--stop", "x.service") == 0
    assert ["systemctl", "stop", "x.service"] not in calls  # 既に止まっているので送らない
    starts = [c[2] for c in calls if c[:2] == ["systemctl", "start"]]
    assert starts == ["x.service", "x.socket"]


# =====================================================================
# atuin のプリセット
# =====================================================================


def test_atuin_data_dir_from_atuin_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """atuin info の client db path から置き場所を決める（版や ATUIN_HOME で場所が変わるため）。"""
    data = tmp_path / "custom" / "atuin"
    data.mkdir(parents=True)
    exe = tmp_path / "atuin"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(bn.shutil, "which", lambda name: str(exe))
    monkeypatch.setattr(bn, "query", lambda cmd: f'client db path: "{data}/history.db"\nkey path: {data}/key\n' if cmd[-1] == "info" else None)
    assert bn.atuin_data_dir(home=tmp_path) == data


def test_atuin_data_dir_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """atuin info が使えなければ、よくある場所のうち history.db があるものを使う。"""
    monkeypatch.setattr(bn, "query", lambda cmd: None)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert bn.atuin_data_dir(home=tmp_path) is None
    d = tmp_path / ".local" / "share" / "atuin"
    d.mkdir(parents=True)
    (d / "history.db").write_bytes(b"")
    assert bn.atuin_data_dir(home=tmp_path) == d


def _spawn_fake_daemon(db: Path, marker: str) -> subprocess.Popen[bytes]:
    """db を開いたまま待つプロセス（atuin のデーモンの代わり）。"""
    code = "import sys, time\nf = open(sys.argv[1], 'ab')\ntime.sleep(300)\n"
    proc = subprocess.Popen([sys.executable, "-c", code, str(db), marker])
    for _ in range(50):  # ファイルを開くまで待つ
        if bn.processes_using([db.parent]):
            break
        time.sleep(0.1)
    return proc


def _kill_marked(marker: str, comm: str) -> None:
    """テストで起動し直されたプロセスを止める。"""
    for pid in bn.find_processes((bn.ProcessMatch(comm, marker),)):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 9)


def test_preset_atuin_stops_and_restarts_daemon(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """デーモンを止めて NOCOW に移し、同じコマンドで起動し直す。"""
    data = work / "atuin-data"
    data.mkdir()
    (data / "history.db").write_bytes(os.urandom(1 << 20))
    (data / "key").write_text("secret\n")
    marker = f"fake-atuin-daemon-{os.getpid()}"
    proc = _spawn_fake_daemon(data / "history.db", marker)
    comm = Path(f"/proc/{proc.pid}/comm").read_text().strip()
    try:
        preset = bn.Preset("テスト用", (), user_level=True,
                           resolve_targets=lambda: (bn.TargetSpec(str(data), False, nocow_all=True),),
                           stop_processes=(bn.ProcessMatch(comm, marker),))
        monkeypatch.setitem(bn.PRESETS, "atuin", preset)
        assert cli(work, "migrate", "--preset", "atuin") == 0
        assert proc.wait(timeout=5) is not None  # 元のデーモンは止まった
        assert bn.walk_tree(data, check_nocow=True).not_nocow == 0 and bn.get_nocow(data) is True
        assert (work / "atuin-data.old").is_dir()
        # 起動し直したデーモンが、新しい側のファイルを開いている
        for _ in range(50):
            if bn.processes_using([data]):
                break
            time.sleep(0.1)
        assert bn.find_processes((bn.ProcessMatch(comm, marker),))
        assert bn.processes_using([data])
        record = bn.State(work / "state.json").migrations[str(data)]
        assert record["stop_processes"] == [{"comm": comm, "arg": marker}]
        assert "env" not in str(record)  # 環境変数は記録しない
    finally:
        proc.kill()
        _kill_marked(marker, comm)


def test_preset_atuin_refuses_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """一般ユーザー専用のプリセットを root で実行したら止める。"""
    monkeypatch.setattr(bn.os, "geteuid", lambda: 0)
    args = bn.build_parser().parse_args(["migrate", "--preset", "atuin"])
    with pytest.raises(bn.MigrationError):
        bn.specs_from_args(args)
