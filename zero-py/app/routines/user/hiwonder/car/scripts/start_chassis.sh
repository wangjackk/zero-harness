#!/bin/zsh
# 启动 ROSOrin 底盘控制(脱离 SSH 会话,常驻运行)
source /home/hiwonder/.zshrc

if pgrep -f "roslaunch.*controller.launch" > /dev/null; then
    echo "CHASSIS_ALREADY_RUNNING"
    exit 0
fi

setsid nohup roslaunch controller controller.launch > /tmp/chassis.log 2>&1 < /dev/null &
disown

# 等待节点完全启动
for i in $(seq 1 15); do
    sleep 2
    if rostopic list 2>/dev/null | grep -q "controller/cmd_vel"; then
        echo "CHASSIS_READY"
        break
    fi
done

echo "=== key topics ==="
rostopic list 2>/dev/null | grep -E "cmd_vel|motor|odom|imu" | head -12
