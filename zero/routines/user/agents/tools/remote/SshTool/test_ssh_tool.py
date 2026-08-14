"""SshTool 自测脚本(不连真实 SSH 服务器).

运行:
    cd zero-harness/zero
    uv run python routines/user/agents/tools/remote/SshTool/test_ssh_tool.py
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[7]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ssh_tool_module = importlib.import_module('zero.routines.user.agents.tools.remote.SshTool.SshTool')
runtime_module = importlib.import_module('zero.routines.user.agents.tools.remote.SshTool.runtime')
shared_module = importlib.import_module('zero.routines.user.agents.tools.remote.SshTool.shared')

Ssh = ssh_tool_module.Ssh
SshInput = ssh_tool_module.SshInput
SshRegistry = runtime_module.SshRegistry
SshSession = runtime_module.SshSession
SshCommandError = runtime_module.SshCommandError
run_shell_command = runtime_module.run_shell_command


class FakeStdin:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, data: str) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.stdout = None
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, process: FakeProcess | None = None) -> None:
        self.process = process or FakeProcess()
        self.closed = False

    async def create_process(self, _shell: str) -> FakeProcess:
        return self.process

    async def start_sftp_client(self) -> object:
        raise AssertionError('test should stub start_sftp_client() explicitly')

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _make_session(*, alias: str = 'robot', target: str = 'root@127.0.0.1') -> SshSession:
    return SshSession(
        session_id=alias,
        target=target,
        host='127.0.0.1',
        username='root',
        port=22,
        identity_file=None,
        strict_host_key_checking=True,
        cwd=None,
        connection=FakeConnection(),
        process=FakeProcess(),
        queue=asyncio.Queue(),
        pump_task=asyncio.create_task(asyncio.sleep(3600)),
    )


def _make_agent() -> Ssh:
    return Ssh.__new__(Ssh)


def test_shared_helpers() -> None:
    host, username = shared_module.parse_target('root@192.168.1.108')
    assert host == '192.168.1.108'
    assert username == 'root'
    assert shared_module.compose_command_with_cwd(command='pwd', cwd='/opt/zero') == "cd /opt/zero && pwd"
    assert shared_module.make_command_non_interactive('sudo du -h /') == 'sudo -n du -h /'
    assert shared_module.make_command_non_interactive('sudo -n du -h /') == 'sudo -n du -h /'
    kwargs = shared_module.build_connect_kwargs(
        host='192.168.1.108',
        username='root',
        port=22,
        identity_file=None,
        password='secret',
        strict_host_key_checking=False,
        project_root=None,
    )
    assert kwargs['known_hosts'] is None
    assert kwargs['port'] == 22
    assert kwargs['password'] == 'secret'
    print('[PASS] test_shared_helpers')


async def test_run_shell_command_success() -> None:
    session = _make_session()
    from types import SimpleNamespace
    original_uuid4 = runtime_module.uuid4
    runtime_module.uuid4 = lambda: SimpleNamespace(hex='abc123')  # type: ignore[assignment]
    try:
        await session.queue.put(('stdout', 'hello\nworld\n'))
        await session.queue.put(('stdout', '\n__TRAE_SSH_DONE_abc123__ 0\n'))
        result = await run_shell_command(session, 'echo hello', timeout=1)
        assert result == 'hello\nworld'
        assert 'echo hello' in session.process.stdin.writes[0]
    finally:
        runtime_module.uuid4 = original_uuid4
        session.pump_task.cancel()
    print('[PASS] test_run_shell_command_success')


async def test_run_shell_command_failure() -> None:
    session = _make_session()
    from types import SimpleNamespace
    original_uuid4 = runtime_module.uuid4
    runtime_module.uuid4 = lambda: SimpleNamespace(hex='deadbeef')  # type: ignore[assignment]
    try:
        await session.queue.put(('stdout', 'permission denied'))
        await session.queue.put(('stdout', '\n__TRAE_SSH_DONE_deadbeef__ 7\n'))
        try:
            await run_shell_command(session, 'whoami', timeout=1)
            raise AssertionError('expected SshCommandError')
        except SshCommandError as exc:
            assert exc.exit_code == 7
            assert 'permission denied' in exc.output
    finally:
        runtime_module.uuid4 = original_uuid4
        session.pump_task.cancel()
    print('[PASS] test_run_shell_command_failure')


async def test_connect_initializes_shell() -> None:
    agent = _make_agent()
    calls: list[str] = []
    fake_process = FakeProcess()
    fake_connection = FakeConnection(process=fake_process)

    async def fake_connect(_host: str, **kwargs) -> FakeConnection:
        assert kwargs['username'] == 'root'
        assert kwargs['password'] == 'hiwonder'
        return fake_connection

    async def fake_run_shell_command(session: SshSession, command: str, *, timeout: int) -> str:
        assert timeout == 5
        calls.append(command)
        return ''

    original_connect = ssh_tool_module.asyncssh.connect
    original_run_shell_command = ssh_tool_module.run_shell_command
    ssh_tool_module.asyncssh.connect = fake_connect  # type: ignore[assignment]
    ssh_tool_module.run_shell_command = fake_run_shell_command  # type: ignore[assignment]
    try:
        result = await agent._connect(
            SshInput(action='connect', alias='robot', target='root@192.168.1.108', cwd='/opt/zero', timeout=5, password='hiwonder'),
            project_root=None,
            session_id='test_session',
        )
        assert 'robot' in result
        assert calls == ['exec 2>&1', 'cd /opt/zero']
        assert SshRegistry.get('test_session', 'robot') is not None
    finally:
        session = SshRegistry.remove('test_session', 'robot')
        if session:
            session.pump_task.cancel()
        ssh_tool_module.asyncssh.connect = original_connect  # type: ignore[assignment]
        ssh_tool_module.run_shell_command = original_run_shell_command  # type: ignore[assignment]
    print('[PASS] test_connect_initializes_shell')


async def test_connect_rejects_duplicate_alias() -> None:
    agent = _make_agent()
    session_existing = _make_session(alias='robot')
    SshRegistry.set('test_session', 'robot', session_existing)
    try:
        try:
            await agent._connect(
                SshInput(action='connect', alias='robot', target='root@10.0.0.5', timeout=5),
                project_root=None,
                session_id='test_session',
            )
            raise AssertionError('expected duplicate alias error')
        except ValueError as exc:
            assert 'alias 已存在' in str(exc)
    finally:
        SshRegistry.remove('test_session', 'robot')
        session_existing.pump_task.cancel()
    print('[PASS] test_connect_rejects_duplicate_alias')


async def test_upload_and_download() -> None:
    agent = _make_agent()
    session = _make_session(alias='server')
    SshRegistry.set('test_session', 'server', session)
    scp_calls: list[tuple[object, object, bool]] = []

    async def fake_scp(src: object, dst: object, *, recurse: bool = False, **kwargs) -> None:
        scp_calls.append((src, dst, recurse))

    async def fake_is_remote_directory(_session: SshSession, remote_path: str) -> bool:
        assert remote_path == '/remote/logs'
        return True

    original_scp = ssh_tool_module.asyncssh.scp
    original_is_remote_directory = ssh_tool_module.is_remote_directory
    ssh_tool_module.asyncssh.scp = fake_scp  # type: ignore[assignment]
    ssh_tool_module.is_remote_directory = fake_is_remote_directory  # type: ignore[assignment]
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            local_dir = tmp / 'payload'
            local_dir.mkdir()
            (local_dir / 'a.txt').write_text('hello', encoding='utf-8')

            upload_result = await agent._upload(
                SshInput(
                    action='upload',
                    alias='server',
                    local_path=str(local_dir),
                    remote_path='/remote/payload',
                ),
                project_root=None,
                session_id='test_session',
            )
            assert '已上传目录' in upload_result
            assert scp_calls[0][2] is True

            download_target = tmp / 'downloads' / 'logs'
            download_result = await agent._download(
                SshInput(
                    action='download',
                    alias='server',
                    remote_path='/remote/logs',
                    local_path=str(download_target),
                ),
                project_root=None,
                session_id='test_session',
            )
            assert '已下载目录' in download_result
            assert scp_calls[1][2] is True
            assert download_target.parent.exists()
    finally:
        SshRegistry.remove('test_session', 'server')
        session.pump_task.cancel()
        ssh_tool_module.asyncssh.scp = original_scp  # type: ignore[assignment]
        ssh_tool_module.is_remote_directory = original_is_remote_directory  # type: ignore[assignment]
    print('[PASS] test_upload_and_download')


async def test_exec_uses_alias() -> None:
    agent = _make_agent()
    session = _make_session(alias='robot')
    session.cwd = '/opt/zero'
    SshRegistry.set('test_session', 'robot', session)

    async def fake_run_shell_command(_session: SshSession, command: str, *, timeout: int) -> str:
        assert command == 'uname -a'
        assert timeout == 3
        return 'Linux test'

    original_run_shell_command = ssh_tool_module.run_shell_command
    ssh_tool_module.run_shell_command = fake_run_shell_command  # type: ignore[assignment]
    try:
        result = await agent._exec(
            SshInput(action='exec', alias='robot', command='uname -a', timeout=3),
            session_id='test_session',
        )
        assert result == 'Linux test'
    finally:
        SshRegistry.remove('test_session', 'robot')
        session.pump_task.cancel()
        ssh_tool_module.run_shell_command = original_run_shell_command  # type: ignore[assignment]
    print('[PASS] test_exec_uses_alias')


async def test_exec_uses_active_session_by_default() -> None:
    agent = _make_agent()
    session = _make_session(alias='robot')
    SshRegistry.set('test_session', 'robot', session)

    async def fake_run_shell_command(_session: SshSession, command: str, *, timeout: int) -> str:
        assert _session.session_id == 'robot'
        assert command == 'pwd'
        assert timeout == 2
        return '/opt/zero'

    original_run_shell_command = ssh_tool_module.run_shell_command
    ssh_tool_module.run_shell_command = fake_run_shell_command  # type: ignore[assignment]
    try:
        result = await agent._exec(
            SshInput(action='exec', command='pwd', timeout=2),
            session_id='test_session',
        )
        assert result == '/opt/zero'
    finally:
        SshRegistry.remove('test_session', 'robot')
        session.pump_task.cancel()
        ssh_tool_module.run_shell_command = original_run_shell_command  # type: ignore[assignment]
    print('[PASS] test_exec_uses_active_session_by_default')


async def test_exec_rewrites_sudo_to_non_interactive() -> None:
    agent = _make_agent()
    session = _make_session(alias='robot')
    SshRegistry.set('test_session', 'robot', session)

    async def fake_run_shell_command(_session: SshSession, command: str, *, timeout: int) -> str:
        assert _session.session_id == 'robot'
        assert command == 'sudo -n du -h /'
        assert timeout == 4
        return 'ok'

    original_run_shell_command = ssh_tool_module.run_shell_command
    ssh_tool_module.run_shell_command = fake_run_shell_command  # type: ignore[assignment]
    try:
        result = await agent._exec(
            SshInput(action='exec', command='sudo du -h /', timeout=4),
            session_id='test_session',
        )
        assert result == 'ok'
    finally:
        SshRegistry.remove('test_session', 'robot')
        session.pump_task.cancel()
        ssh_tool_module.run_shell_command = original_run_shell_command  # type: ignore[assignment]
    print('[PASS] test_exec_rewrites_sudo_to_non_interactive')


async def test_multiple_connections_same_agent() -> None:
    """同 agent session 下可同时保持多条连接, 按 alias 路由."""
    agent = _make_agent()
    session_robot = _make_session(alias='robot', target='root@192.168.1.20')
    session_server = _make_session(alias='server', target='root@10.0.0.5')
    SshRegistry.set('test_session', 'robot', session_robot)
    SshRegistry.set('test_session', 'server', session_server)

    async def fake_run_shell_command(_session: SshSession, command: str, *, timeout: int) -> str:
        return f'{_session.session_id}:{command}'

    original_run_shell_command = ssh_tool_module.run_shell_command
    ssh_tool_module.run_shell_command = fake_run_shell_command  # type: ignore[assignment]
    try:
        r1 = await agent._exec(
            SshInput(action='exec', alias='robot', command='hostname', timeout=2),
            session_id='test_session',
        )
        r2 = await agent._exec(
            SshInput(action='exec', alias='server', command='hostname', timeout=2),
            session_id='test_session',
        )
        assert r1 == 'robot:hostname'
        assert r2 == 'server:hostname'

        listing = agent._list_sessions('test_session')
        assert 'robot' in listing and 'server' in listing
    finally:
        SshRegistry.remove('test_session', 'robot')
        SshRegistry.remove('test_session', 'server')
        session_robot.pump_task.cancel()
        session_server.pump_task.cancel()
        ssh_tool_module.run_shell_command = original_run_shell_command  # type: ignore[assignment]
    print('[PASS] test_multiple_connections_same_agent')


async def main() -> None:
    test_shared_helpers()
    await test_run_shell_command_success()
    await test_run_shell_command_failure()
    await test_connect_initializes_shell()
    await test_connect_rejects_duplicate_alias()
    await test_upload_and_download()
    await test_exec_uses_alias()
    await test_exec_uses_active_session_by_default()
    await test_exec_rewrites_sudo_to_non_interactive()
    await test_multiple_connections_same_agent()
    print('\nall ssh client tests passed')


if __name__ == '__main__':
    asyncio.run(main())
