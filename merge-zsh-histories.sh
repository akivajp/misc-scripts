#!/bin/bash

# 2つ以上のzsh historyファイルをマージして標準出力に書き出す

source_dir="$(cd "$(dirname "${BASH_SOURCE:-${(%):-%N}}")"; pwd)"
source ${source_dir}/common.sh

set -e

if [ $# -lt 2 ]; then
    echo "Usage: $0 <history1> <history2> ..."
    exit 1
fi

show-info "Merging history files: ${@}" > /dev/stderr

for history in "${@}"; do
    if [ ! -f "$history" ]; then
        show-error "File not found: $history" > /dev/stderr
        exit 1
    fi
done

cat "${@}" | \
    awk -v date="WILL_NOT_APPEAR$(date +"%s")" \
        '{if (sub(/\\$/,date)) printf "%s", $0; else print $0}' | \
    LC_ALL=C sort -u | \
    awk -v date="WILL_NOT_APPEAR$(date +"%s")" '{gsub('date',"\\\n"); print $0}'
