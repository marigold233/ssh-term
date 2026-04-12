"""SFTP file transfer operations."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

import asyncssh


@dataclass
class RemoteEntry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    mtime: float = 0


class SFTPManager:
    def __init__(self, sftp: asyncssh.SFTPClient) -> None:
        self.sftp = sftp

    async def listdir(self, remote_path: str) -> list[RemoteEntry]:
        entries: list[RemoteEntry] = []
        try:
            for attr in await self.sftp.readdir(remote_path):
                # asyncssh SFTPName object contains filename and attrs
                is_dir = stat.S_ISDIR(attr.attrs.permissions or 0)
                full = remote_path.rstrip("/") + "/" + attr.filename
                entries.append(
                    RemoteEntry(
                        name=attr.filename,
                        path=full,
                        is_dir=is_dir,
                        size=attr.attrs.size or 0,
                        mtime=attr.attrs.mtime or 0,
                    )
                )
        except asyncssh.SFTPError:
            pass
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    async def download(
        self,
        remote_path: str,
        local_path: str,
    ) -> None:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        await self.sftp.get(remote_path, local_path)

    async def upload(
        self,
        local_path: str,
        remote_path: str,
    ) -> None:
        await self.sftp.put(local_path, remote_path)

    async def mkdir(self, remote_path: str) -> None:
        try:
            await self.sftp.mkdir(remote_path)
        except asyncssh.SFTPError:
            pass

    async def remove(self, remote_path: str) -> None:
        await self.sftp.remove(remote_path)

    async def stat(self, remote_path: str) -> asyncssh.SFTPAttrs | None:
        try:
            return await self.sftp.stat(remote_path)
        except asyncssh.SFTPError:
            return None

    async def upload_recursive(
        self,
        local_dir: str,
        remote_dir: str,
    ) -> int:
        """Upload a directory recursively. Returns number of files transferred."""
        count = 0
        for dirpath, dirnames, filenames in os.walk(local_dir):
            rel = os.path.relpath(dirpath, local_dir)
            remote_sub = remote_dir if rel == "." else remote_dir.rstrip("/") + "/" + rel.replace(os.sep, "/")
            await self.mkdir(remote_sub)
            for fname in filenames:
                local_file = os.path.join(dirpath, fname)
                remote_file = remote_sub.rstrip("/") + "/" + fname
                await self.sftp.put(local_file, remote_file)
                count += 1
        return count

    async def download_recursive(
        self,
        remote_dir: str,
        local_dir: str,
    ) -> int:
        """Download a directory recursively. Returns number of files transferred."""
        count = 0
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        for entry in await self.listdir(remote_dir):
            if entry.is_dir:
                sub_local = os.path.join(local_dir, entry.name)
                count += await self.download_recursive(entry.path, sub_local)
            else:
                local_file = os.path.join(local_dir, entry.name)
                await self.sftp.get(entry.path, local_file)
                count += 1
        return count

    async def cwd(self) -> str:
        # get_realpath converts . to absolute path roughly
        return await self.sftp.realpath(".")

    def close(self) -> None:
        self.sftp.exit()
