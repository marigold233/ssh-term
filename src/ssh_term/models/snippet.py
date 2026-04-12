"""Command/Script snippet data model."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class Snippet:
    name: str  # Short name for quick search
    content: str  # The actual script/command
    description: str = ""  # Notes/Remarks
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Snippet:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def touch(self) -> None:
        self.updated_at = datetime.now().isoformat()
