#!/bin/bash

source_dir="$(cd "$(dirname "${BASH_SOURCE:-${(%):-%N}}")"; pwd)"
source ${source_dir}/common.sh

set -e

PCLOUD=pcloud
PCLOUD_LOG_DIR="${HOME}/logs"
PCLOUD_LOG="${PCLOUD_LOG_DIR}/pcloud.log"
PCLOUD_PREV_LOG="${PCLOUD_LOG_DIR}/pcloud.prev.log"

check-command ${PCLOUD}

if [ ! -d ${PCLOUD_LOG_DIR} ]; then
    show-exec mkdir -p ${PCLOUD_LOG_DIR}
fi

if [ -f ${PCLOUD_LOG} ]; then
    show-exec mv ${PCLOUD_LOG} ${PCLOUD_PREV_LOG}
fi

show-exec ${PCLOUD} 2\> /dev/stdout \| tee ${PCLOUD_LOG}

