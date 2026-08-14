"""dial-in 业务级 fixture:routine 当 grpc client 拨 kernel server,跑真 RoutineHub.

注册 Quick(不占模块,start sleep 后自然完成,return {ok, echo}).连上后 transport
自动 Req 拉 module.tree + push catalog(_post_connect),kernel 收到注册路由表后
Execute quick → created→start→stopped 全链路 + result 回带.

验证 routine 作为 client 的业务级全链路(Phase ② + module.tree Req pull + 重连),
区别于 _dialin_client.py 的传输级握手(裸 transport echo).被 Go test 用 exec 调起,
argv[1] = kernel addr.常驻(不退出),靠 Go 侧 kill 终止.
"""
import asyncio
import sys

from routine import Routine, Routines, start_client


class Quick(Routine):
    """不占模块,start sleep 后自然完成,return {ok, echo}."""

    async def run(self, kwargs):
        await asyncio.sleep(0.3)
        return {'ok': True, 'echo': kwargs.get('msg', '')}


def get_routines() -> Routines:
    rs = Routines()
    rs.register(Quick)
    return rs


def get_modules():
    return ['body', 'leg', 'mouth', 'head', 'figure', 'core']


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('usage: _dialin_execute.py <kernel-addr>', file=sys.stderr)
        sys.exit(2)
    asyncio.run(start_client(get_routines(), modules=get_modules(),
                             address=sys.argv[1]))
