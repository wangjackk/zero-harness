from __future__ import annotations

from typing import Any

from .runtime import SshSession


async def is_remote_directory(session: SshSession, remote_path: str) -> bool:
    sftp = await session.connection.start_sftp_client()
    try:
        return await sftp.isdir(remote_path)
    finally:
        await close_sftp_client(sftp)


async def close_sftp_client(sftp: Any) -> None:
    try:
        sftp.exit()
    except Exception:
        pass

    wait_closed = getattr(sftp, 'wait_closed', None)
    if callable(wait_closed):
        try:
            await wait_closed()
        except Exception:
            pass
