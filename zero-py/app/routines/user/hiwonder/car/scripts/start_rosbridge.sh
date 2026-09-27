#!/bin/zsh
source /home/hiwonder/.zshrc > /dev/null 2>&1
if pgrep -f "rosbridge_websocket" > /dev/null; then
    echo "ROSBRIDGE_ALREADY_RUNNING"
else
    setsid nohup roslaunch bringup rosbridge.launch > /tmp/rosbridge.log 2>&1 < /dev/null &
    disown
    for i in $(seq 1 10); do
        sleep 1
        if ss -tln 2>/dev/null | grep -q 9090; then
            echo "ROSBRIDGE_READY"
            break
        fi
    done
fi
ss -tln 2>/dev/null | grep 9090 && echo "port 9090 listening"
