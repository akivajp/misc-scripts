#!/bin/bash

# 現在のファイルを削除
sudo rm /etc/resolv.conf

# systemd-resolved のスタブファイルへのシンボリックリンクを作成
sudo ln -s /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf

# systemd-resolved を再起動して変更を反映
# (これも必要であることを確認済み)
sudo systemctl restart systemd-resolved

sudo systemctl stop tailscaled
#sudo rm -rf /var/lib/tailscale/tailscaled.state
sudo systemctl start tailscaled
sudo tailscale up --accept-dns=true

# 仮想マシンのアダプタを再有効化
sudo virsh net-start default

# 仮想マシンも停止していたら起動
sudo virsh start windows11

