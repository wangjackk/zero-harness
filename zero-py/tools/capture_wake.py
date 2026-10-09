"""临时抓包: 订阅 voice.wake 40s, 打印事件 (设备端视角对时延用)."""
import time
import zmq

ctx = zmq.Context()
s = ctx.socket(zmq.SUB)
s.setsockopt(zmq.SUBSCRIBE, b'voice.wake')
s.connect('tcp://127.0.0.1:6667')

t0 = time.time()
n = 0
while time.time() - t0 < 40:
    if s.poll(500):
        print(f'recv_wall={time.time():.2f}', s.recv().decode(errors='replace')[:200], flush=True)
        n += 1
print(f'CAPTURE_DONE total={n}')
