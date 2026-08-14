"""dial-in 传输级 fixture:GrpcClientTransport 拨 Go kernel server,echo 握手.

Go 侧 TestPythonDialInTransport 起 NewServer(dial-in),onAccept 收到 lifecycle.created
后 echo 回 lifecycle.start.本脚本:拨入 → 发 created → 等收 start → 发 started → 退出 0.
证明 Python GrpcClientTransport ↔ Go ServerConn 双向 lifecycle 通(Phase ① 传输级).

被 Go test 用 exec 调起,argv[1] = kernel server addr.成功 exit 0,失败 exit 1.
"""
import asyncio
import sys

from routine import GrpcClientTransport


async def main(addr: str) -> int:
    t = GrpcClientTransport(addr)
    got_start = asyncio.Event()

    async def on_inbound(peer_id: str, msg: dict) -> None:
        if msg.get('event') == 'lifecycle.start':
            got_start.set()

    t.set_inbound(on_inbound)
    await t.start()

    # created 可能撞 accept 竞速(channel_ready 不保证 server Stream handler 已跑),
    # 重发覆盖:每 0.3s 发一次 created,直到收到 start 或耗尽重试.
    for _ in range(20):
        await t.send_event({'event': 'lifecycle.created', 'id': '1', 'name': 'X'})
        try:
            await asyncio.wait_for(got_start.wait(), timeout=0.3)
            break
        except asyncio.TimeoutError:
            continue
    else:
        print('TIMEOUT: no lifecycle.start echo from kernel', file=sys.stderr)
        await t.stop()
        return 1

    await t.send_event({'event': 'lifecycle.started', 'id': '1'})
    # 让 Go 收到 started 再退(否则 cmd.Wait 可能先于 Go 处理 started)
    await asyncio.sleep(0.3)
    await t.stop()
    return 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('usage: _dialin_client.py <kernel-addr>', file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
