"""SSH connection lifecycle management."""

from __future__ import annotations

import os
from pathlib import Path

import asyncssh

from ssh_term.models.connection import SSHConnection


class SSHManager:
    def __init__(self) -> None:
        self._clients: dict[str, asyncssh.SSHClientConnection] = {}

    async def connect(
        self,
        conn: SSHConnection,
        password: str | None = None,
    ) -> asyncssh.SSHClientConnection:
        existing = self.get_client(conn.id)
        if existing:
            return existing

        connect_kwargs: dict = {
            "host": conn.host,
            "port": conn.port,
            "username": conn.username,
            "known_hosts": None,
        }

        if conn.auth_method == "key":
            key_path = os.path.expanduser(conn.private_key_path)
            if Path(key_path).exists():
                connect_kwargs["client_keys"] = [key_path]
        elif conn.auth_method == "password" and password:
            connect_kwargs["password"] = password
        elif conn.auth_method == "agent":
            pass # agent is implicitly used by asyncssh unless disabled

        client = await asyncssh.connect(**connect_kwargs)
        self._clients[conn.id] = client
        return client

    def get_client(self, conn_id: str) -> asyncssh.SSHClientConnection | None:
        return self._clients.get(conn_id)

    async def open_shell(
        self, conn_id: str, cols: int = 80, rows: int = 24
    ) -> asyncssh.SSHClientProcess:
        client = self._clients.get(conn_id)
        if not client:
            raise RuntimeError(f"No active connection for {conn_id}")
        process = await client.create_process(
            term_type="xterm-256color", 
            term_size=(cols, rows),
            env={"TERM": "xterm-256color", "COLORTERM": "truecolor"}
        )
        return process

    async def open_sftp(self, conn_id: str) -> asyncssh.SFTPClient:
        client = self._clients.get(conn_id)
        if not client:
            raise RuntimeError(f"No active connection for {conn_id}")
        return await client.start_sftp_client()

    def disconnect(self, conn_id: str) -> None:
        client = self._clients.pop(conn_id, None)
        if client:
            client.close()

    def disconnect_all(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    def is_connected(self, conn_id: str) -> bool:
        client = self._clients.get(conn_id)
        if not client:
            return False
        # If the client exists in our active dictionary, we consider it connected
        # Disconnections are handled when the terminal emulator raises Disconnected.
        return True
