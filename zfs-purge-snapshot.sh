#!/bin/sh

PATH=$PATH:/sbin:/usr/sbin

ZFS_list_snap='zfs list -H -o name -d 1 -t snapshot'

if [ $# -le 0 ]; then
    echo "Usage: ${0##*/} zfs [zfs ...]" 1>&2
    exit 2
fi

for fs in "$@"; do
    if [ $(${ZFS_list_snap} "${fs}" | wc -l) = 0 ]; then
            continue
    fi
    snap0=$(${ZFS_list_snap} "${fs}" | head -1)
    snap1=$(${ZFS_list_snap} "${fs}" | tail -1 | sed 's/.*@//')
    echo "[exec] zfs destroy "${snap0}%${snap1}" || exit"
    zfs destroy "${snap0}%${snap1}" || exit
done

