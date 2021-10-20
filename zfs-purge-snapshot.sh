#!/bin/bash

PATH=$PATH:/sbin:/usr/sbin

args=()

depth=1
error=""
while (( $# > 0 ))
do
    case $1 in
        -d | --depth)
            if [[ -z "$2" ]]; then
                echo "-d|--depth requires an argument" > /dev/stderr
                exit 1
            fi
            depth=$2
            shift
            ;;
        --depth=*)
            depth=$(echo $1 | sed -e 's/^--depth=//')
            ;;
        *)
            args+=($1)
    esac
    shift
done

if [ ${#args} -lt 1 ]; then
    echo "Usage: ${0##*/} [-d|--depth depth] zfs [zfs ...]" 1>&2
    exit 1
fi

echo "depth: ${depth}"
echo "args: ${args[@]}"

ZFS_list_snap="zfs list -H -o name -d ${depth} -t snapshot"

for fs in "${args[@]}"; do
    echo "[exec] ${ZFS_list_snap} ${fs}"
    snapshots=($(${ZFS_list_snap} "${fs}"))
    if [ ${#snapshots} -lt 1 ]; then
        echo "no snapshots are found"
    else
        for snapshot in "${snapshots[@]}"; do
            echo "[exec] zfs destroy ${snapshot} || exit"
            zfs destroy ${snapshot} || exit
        done
    fi
done

