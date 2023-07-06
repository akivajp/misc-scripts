#!/bin/bash

clear="\033[0m"  
red="\033[31m"  
yellow="\033[33m"                                      
cyan="\033[36m"  

# 実行内容を表示してから実行
show-exec() {                            
    timestamp=$(date "+%Y/%m/%d %H:%M:%S")
    echo -e "${yellow}[exec at ${timestamp}] $@${clear}"
    eval "$@"                                                  
}        

# メッセージを表示
show-info() {
    timestamp=$(date "+%Y/%m/%d %H:%M:%S")
    echo -e "${cyan}[info at ${timestamp}] $@${clear}"
}

# エラーメッセージを表示
show-error() {
    timestamp=$(date "+%Y/%m/%d %H:%M:%S")
    echo -e "${red}[error at ${timestamp}] $@${clear}"
}
