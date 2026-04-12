"""SSH connection lifecycle management."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

import asyncssh

from ssh_term.models.connection import SSHConnection

if TYPE_CHECKING:
    pass


class SSHManager:
    def __init__(self) -> None:
        self._clients: dict[str, asyncssh.SSHClientConnection] = {}
        # Tracks active port-forward listeners keyed by conn.id
        self._port_forward_listeners: dict[str, list[asyncssh.SSHListener]] = {}

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _build_connect_kwargs(
        self,
        conn: SSHConnection,
        password: str | None = None,
    ) -> dict:
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
        # "agent" — asyncssh uses agent automatically
        return kwargs

    async def _start_port_forwards(
        self,
        conn: SSHConnection,
        client: asyncssh.SSHClientConnection,
    ) -> None:
        """Start all configured port-forward tunnels for a connection."""
        if not conn.port_forwards:
            return
        listeners: list[asyncssh.SSHListener] = []
        for rule in conn.port_forwards:
            # Expected format: "local_port:remote_host:remote_port"
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
                listener = await client.forward_local_port(
                    "127.0.0.1", local_port, remote_host, remote_port
                )
                listeners.append(listener)
            except Exception:
                pass  # Best-effort; failures are non-fatal
        self._port_forward_listeners[conn.id] = listeners

    def _stop_port_forwards(self, conn_id: str) -> None:
        for listener in self._port_forward_listeners.pop(conn_id, []):
            try:
                listener.close()
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
        """Establish an SSH connection, transparently handling jump servers
        and port forwarding.

        Args:
            conn: The target connection to open.
            password: Plain-text password (already decrypted) if auth is password.
            all_connections: Full list of saved connections; required to resolve
                             proxy_jump_id references.
            auth_manager: AuthManager instance for decrypting jump server passwords.
        """
        existing = self.get_client(conn.id)
        if existing:
            return existing

        connect_kwargs = self._build_connect_kwargs(conn, password)

        # ── Jump / Proxy server setup ─────────────────────────────────────
        if conn.proxy_jump_id and all_connections:
            jump_conn = next(
                (c for c in all_connections if c.id == conn.proxy_jump_id), None
            )
            if jump_conn:
                # Ensure the jump host itself is connected first
                jump_client = await self.connect(
                    jump_conn,
                    password=self._decrypt_password(jump_conn, auth_manager),
                    all_connections=all_connections,
                    auth_manager=auth_manager,
                )
                # Open a tunnel through the jump host to the target
                tunnel = await jump_client.create_connection(
                    asyncssh.SSHTCPSession,
                    conn.host,
                    conn.port,
                )
                connect_kwargs["tunnel"] = jump_client

        client = await asyncssh.connect(**connect_kwargs)
        self._clients[conn.id] = client

        # ── Port forwarding ──────────────────────────────────────────────
        await self._start_port_forwards(conn, client)

        return client

    @staticmethod
    def _decrypt_password(conn: SSHConnection, auth_manager) -> str | None:
        """Safely decrypt a password if auth_manager is available."""
        if (
            auth_manager
            and conn.auth_method == "password"
            and conn.password_encrypted
        ):
            try:
                return auth_manager.decrypt(conn.password_encrypted)
            except Exception:
                pass
        return None

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
            env={"TERM": "xterm-256color", "COLORTERM": "truecolor"},
        )
        return process

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

    def get_active_port_forwards(self, conn_id: str) -> list[str]:
        """Return list of active forward rule strings for a connection."""
        listeners = self._port_forward_listeners.get(conn_id, [])
        return [str(l) for l in listeners]
