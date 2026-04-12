"""SSH Connection data model."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Literal


@dataclass
class SSHConnection:
    name: str
    host: str
    port: int = 22
    username: str = ""
    ip: str = ""
    auth_method: Literal["key", "password", "agent"] = "key"
    private_key_path: str = "~/.ssh/id_ed25519"
    password_encrypted: str = ""
    tags: list[str] = field(default_factory=list)
    color_label: str = "blue"
    last_connected: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Jump server (bastion/proxy) - ID of another SSHConnection to tunnel through
    proxy_jump_id: str = ""
    # Port forwarding rules: "local_port:remote_host:remote_port"  e.g. "8080:localhost:80"
    port_forwards: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SSHConnection:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def touch(self) -> None:
        self.last_connected = datetime.now().isoformat()
