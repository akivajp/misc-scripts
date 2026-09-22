#!/bin/bash

# パッケージ更新やサーバー再起動によって切断された tailscale の接続を復旧するスクリプト
#
# 【DNSに関する前提・注意】
# 外向け (インターネット) の上流DNSは systemd-resolved の Global 設定として
#   /etc/systemd/resolved.conf.d/90-upstream-dns.conf
# に記述しておくこと。
#
# /etc/resolv.conf に nameserver を直接書き込んではいけない。
# 直接書き込むと systemd-resolved のスタブ (127.0.0.53) を経由しなくなり、
# tailscale が登録する `~ts.net` のルーティングドメインが無視されるため、
# MagicDNS (*.ts.net) による tailnet 内のホスト名解決ができなくなる。
#
# 正しく設定されていれば、以下のように問い合わせ先が振り分けられる:
#   *.ts.net  -> tailscale0 リンクの DNS (100.100.100.100) = MagicDNS
#   それ以外   -> Global の上流DNS (ルータ / パブリックDNS)

source_dir="$(cd "$(dirname "${BASH_SOURCE:-${(%):-%N}}")"; pwd)"
source ${source_dir}/common.sh

check-command tailscale
check-command resolvectl
check-command jq

# /etc/resolv.conf を systemd-resolved のスタブへのシンボリックリンクにする
# -f: 既存のファイル・リンクがあれば置き換える (冪等に実行可能)
# -n: リンク先がディレクトリへのシンボリックリンクだった場合に辿らない
show-exec sudo ln -sfn /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf

# systemd-resolved を再起動して変更を反映
# (これも必要であることを確認済み)
# 自動生成ファイルである /run/systemd/resolve/stub-resolv.conf が
# 過去に手動編集されていた場合も、この再起動で正しい内容に再生成される
show-exec sudo systemctl restart systemd-resolved

# tailscaled を再起動し、DNS 設定を systemd-resolved へ登録し直す
show-exec sudo systemctl stop tailscaled
#show-exec sudo rm -rf /var/lib/tailscale/tailscaled.state
show-exec sudo systemctl start tailscaled
show-exec sudo tailscale up --accept-dns=true

# 仮想マシンのアダプタを再有効化 (既に起動済みの場合はエラーになるが無視する)
show-exec sudo virsh net-start default || true

# 仮想マシンも停止していたら起動 (既に起動済みの場合はエラーになるが無視する)
show-exec sudo virsh start windows11 || true

# --- DNS の疎通確認 ---------------------------------------------------------

show-info "DNS の疎通を確認します"

exit_code=0

# 1. systemd-resolved が外向けの上流DNSを把握しているか
#    tailscale0 リンクの DNS は `~ts.net` 専用のルーティングドメインに紐づくため、
#    それを除いた Global / 物理リンクに DNS が無いと外向けの名前解決は必ず失敗する
if ! resolvectl dns | grep -v '(tailscale0)' | grep -qE ':[[:space:]]+[^[:space:]]'; then
    show-error "systemd-resolved に外向けの上流DNSが1つも登録されていません"
    show-error "  -> /etc/systemd/resolved.conf.d/90-upstream-dns.conf に DNS= を設定して下さい"
    exit_code=1
fi

# 2. 外向けの名前解決 (Global の上流DNS経由)
#    --cache=no でキャッシュヒットによる偽陽性を避ける
if resolvectl query --cache=no example.com > /dev/null 2>&1; then
    show-info "  [OK] 外向けDNS: example.com を解決できました"
else
    show-error "  [NG] 外向けDNS: example.com を解決できません"
    show-error "       -> 'resolvectl status' の Global / Link に DNS Servers があるか確認して下さい"
    exit_code=1
fi

# 3. tailnet 内の名前解決 (MagicDNS 経由)
#    自ホストの FQDN (末尾のドットは除去) を systemd-resolved 経由で引けるか確認する
self_dns_name="$(tailscale status --json 2>/dev/null | jq -r '.Self.DNSName // empty' | sed 's/\.$//')"
if [ -z "${self_dns_name}" ]; then
    show-error "  [NG] MagicDNS: tailscale から自ホストの FQDN を取得できませんでした"
    exit_code=1
elif resolvectl query --cache=no "${self_dns_name}" > /dev/null 2>&1; then
    show-info "  [OK] MagicDNS: ${self_dns_name} を解決できました"
else
    show-error "  [NG] MagicDNS: ${self_dns_name} を解決できません"
    show-error "       -> /etc/resolv.conf が 127.0.0.53 (スタブ) を指しているか確認して下さい"
    exit_code=1
fi

if [ ${exit_code} -eq 0 ]; then
    show-info "外向けDNSと MagicDNS の両方が正常に動作しています"
fi

exit ${exit_code}
