#!/bin/bash

# 最新の ${LEAVE_COUNT} 件だけ残して古い bpool/BOOT のスナップショットを削除

LEAVE_COUNT=5

echo "[exec] zfs list -r -t snapshot bpool/BOOT -o name,used,referenced,creation | head -n -${LEAVE_COUNT} | cut -c 35-40 | sudo xargs -n 1 zsysctl state remove --system"
zfs list -r -t snapshot bpool/BOOT -o name,used,referenced,creation | head -n -${LEAVE_COUNT} | cut -c 35-40 | sudo xargs -n 1 zsysctl state remove --system

