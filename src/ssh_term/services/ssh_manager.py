"""SSH connection lifecycle management."""

from __future__ import annotations

import os
from pathlib import Path

import asyncssh

from ssh_term.models.connection import SSHConnection


class SSHManager:
    def __init__(self) -> None:
        self._clients: dict[str, asyncssh.SSHClientConnection] = {}
        self._listeners: dict[str, list[asyncssh.SSHListener]] = {}

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _build_kwargs(self, conn: SSHConnection, password: str | None) -> dict:
        kwargs: dict = {
            "host": conn.host,
            "port": conn.port,
            "username": conn.username,
            "known_hosts": None,
        }
        if conn.auth_method == "key":
            key_path = os.path.expanduser(conn.private_key_path)
            if Path(key_path).exists():
                kwargs["client_keys"] = [key_path]
        elif conn.auth_method == "password" and password:
            kwargs["password"] = password
        # "agent" — asyncssh uses agent implicitly
        return kwargs

    @staticmethod
    def _decrypt_pw(conn: SSHConnection, auth_manager) -> str | None:
        if auth_manager and conn.auth_method == "password" and conn.password_encrypted:
            try:
                return auth_manager.decrypt(conn.password_encrypted)
            except Exception:
                pass
        return None

    async def _start_port_forwards(
        self, conn: SSHConnection, client: asyncssh.SSHClientConnection
    ) -> None:
        listeners: list[asyncssh.SSHListener] = []
        for rule in conn.port_forwards:
            parts = rule.strip().split(":")
            if len(parts) != 3:
                continue
            try:
                local_port = int(parts[0])
                remote_host = parts[1]
                remote_port = int(parts[2])
            except ValueError:
                continue
            try:
                lsn = await client.forward_local_port(
                    "127.0.0.1", local_port, remote_host, remote_port
                )
                listeners.append(lsn)
            except Exception:
                pass  # Non-fatal
        self._listeners[conn.id] = listeners

    def _stop_port_forwards(self, conn_id: str) -> None:
        for lsn in self._listeners.pop(conn_id, []):
            try:
                lsn.close()
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    async def connect(
        self,
        conn: SSHConnection,
        password: str | None = None,
        all_connections: list[SSHConnection] | None = None,
        auth_manager=None,
    ) -> asyncssh.SSHClientConnection:
        """Connect to an SSH server, transparently handling jump hosts and port forwards.

        Args:
            conn:             Target connection to open.
            password:         Pre-decrypted plain-text password (if applicable).
            all_connections:  Full connection list — required to resolve proxy_jump_id.
            auth_manager:     AuthManager for decrypting jump-server passwords.
        """
        existing = self.get_client(conn.id)
        if existing:
            return existing

        kwargs = self._build_kwargs(conn, password)

        # ── Proxy / Jump host ─────────────────────────────────────────────
        if conn.proxy_jump_id and all_connections:
            jump = next((c for c in all_connections if c.id == conn.proxy_jump_id), None)
            if jump:
                # Recursively ensure jump host is connected
                jump_pw = self._decrypt_pw(jump, auth_manager)
                jump_client = await self.connect(
                    jump,
                    password=jump_pw,
                    all_connections=all_connections,
                    auth_manager=auth_manager,
                )
                kwargs["tunnel"] = jump_client

        client = await asyncssh.connect(**kwargs)
        self._clients[conn.id] = client

        # ── Port forwarding ───────────────────────────────────────────────
        if conn.port_forwards:
            await self._start_port_forwards(conn, client)

        return client

    def get_client(self, conn_id: str) -> asyncssh.SSHClientConnection | None:
        client = self._clients.get(conn_id)
        if client and client.is_closing():
            self._clients.pop(conn_id, None)
            return None
        return client

    async def open_shell(
        self, conn_id: str, cols: int = 80, rows: int = 24
    ) -> asyncssh.SSHClientProcess:
        client = self._clients.get(conn_id)
        if not client:
            raise RuntimeError(f"No active connection for {conn_id}")
        return await client.create_process(
            term_type="xterm-256color",
            term_size=(cols, rows),
            env={"TERM": "xterm-256color", "COLORTERM": "truecolor"},
        )

    async def open_sftp(self, conn_id: str) -> asyncssh.SFTPClient:
        client = self._clients.get(conn_id)
        if not client:
            raise RuntimeError(f"No active connection for {conn_id}")
        return await client.start_sftp_client()

    def disconnect(self, conn_id: str) -> None:
        self._stop_port_forwards(conn_id)
        client = self._clients.pop(conn_id, None)
        if client:
            client.close()

    def disconnect_all(self) -> None:
        for conn_id in list(self._clients.keys()):
            self.disconnect(conn_id)

    def is_connected(self, conn_id: str) -> bool:
        return conn_id in self._clients
