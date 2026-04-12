"""Add/Edit connection modal form."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Input, Button, Select
from textual.containers import Vertical, Horizontal, Center
from textual import on

from ssh_term.models.connection import SSHConnection


class ConnectionFormModal(ModalScreen[SSHConnection | None]):
    CSS = """
    ConnectionFormModal {
        align: center middle;
    }
    ConnectionFormModal #form-container {
        width: 66;
        height: auto;
        max-height: 42;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
        overflow-y: auto;
    }
    ConnectionFormModal .form-title {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    ConnectionFormModal .section-header {
        text-style: bold;
        color: $secondary;
        margin-top: 1;
        padding: 0 0;
        border-bottom: solid $panel;
    }
    ConnectionFormModal .form-label {
        margin-top: 1;
        color: $text-muted;
    }
    ConnectionFormModal Input {
        margin-bottom: 0;
    }
    ConnectionFormModal Select {
        margin-bottom: 0;
    }
    ConnectionFormModal .form-error {
        color: $error;
        text-align: center;
        margin-top: 1;
    }
    ConnectionFormModal .form-hint {
        color: $text-muted;
        margin-top: 0;
        padding: 0 1;
    }
    ConnectionFormModal Horizontal {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    ConnectionFormModal Horizontal Button {
        margin: 0 1;
    }
    """

    def __init__(self, connection: SSHConnection | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.connection = connection

    def compose(self) -> ComposeResult:
        c = self.connection
        title = "Edit Connection" if c else "Add Connection"

        # Build jump server options from config
        connections = []
        try:
            connections = self.app.config_manager.connections
        except Exception:
            pass

        jump_options = [("(None)", "")]
        for conn in connections:
            if c and conn.id == c.id:
                continue  # Skip self
            jump_options.append((f"{conn.name} ({conn.host})", conn.id))

        # Current jump selection
        current_jump = c.proxy_jump_id if c else ""
        current_pf = "\n".join(c.port_forwards) if c and c.port_forwards else ""

        with Center():
            with Vertical(id="form-container"):
                yield Static(title, classes="form-title")

                # ─── Basic Info ────────────────────────────────────────────
                yield Static("── Basic Info ──────────────────────────────", classes="section-header")
                yield Static("Name", classes="form-label")
                yield Input(value=c.name if c else "", placeholder="e.g. Prod Server", id="name")
                yield Static("Host / IP", classes="form-label")
                yield Input(value=c.host if c else "", placeholder="e.g. 192.168.1.50", id="ip")
                yield Static("Port", classes="form-label")
                yield Input(value=str(c.port) if c else "22", placeholder="22", id="port")
                yield Static("Username", classes="form-label")
                yield Input(value=c.username if c else "", placeholder="e.g. deploy", id="username")

                # ─── Auth ─────────────────────────────────────────────────
                yield Static("── Authentication ──────────────────────────", classes="section-header")
                yield Static("Auth Method", classes="form-label")
                yield Select(
                    [("SSH Key", "key"), ("Password", "password"), ("SSH Agent", "agent")],
                    value=c.auth_method if c else "key",
                    id="auth_method",
                )
                yield Static("Private Key Path", classes="form-label")
                yield Input(
                    value=c.private_key_path if c else "~/.ssh/id_ed25519",
                    placeholder="~/.ssh/id_ed25519",
                    id="key_path",
                )
                yield Static("Password (for password auth)", classes="form-label")
                yield Input(value="", password=True, placeholder="SSH password", id="password")

                # ─── Tags ─────────────────────────────────────────────────
                yield Static("── Tags ────────────────────────────────────", classes="section-header")
                yield Static("Tags (comma-separated)", classes="form-label")
                yield Input(
                    value=", ".join(c.tags) if c and c.tags else "",
                    placeholder="e.g. prod, web",
                    id="tags",
                )

                # ─── Tunnel / Jump Server ─────────────────────────────────
                yield Static("── Tunnel (Jump Server / Bastion) ──────────", classes="section-header")
                yield Static("Proxy Jump Server", classes="form-label")
                yield Select(jump_options, value=current_jump or "", id="proxy_jump")
                yield Static("Port Forwards (one per line: localport:host:remoteport)", classes="form-label")
                yield Input(
                    value=current_pf,
                    placeholder="e.g. 8080:localhost:80  or  3306:db-internal:3306",
                    id="port_forwards",
                )
                yield Static(
                    "Format: LOCAL_PORT:REMOTE_HOST:REMOTE_PORT  —  access via 127.0.0.1:LOCAL_PORT",
                    classes="form-hint",
                )

                # ─── Buttons ──────────────────────────────────────────────
                yield Static("", id="form-error", classes="form-error")
                with Horizontal():
                    yield Button("Cancel", variant="default", id="cancel-btn")
                    yield Button("Save", variant="primary", id="save-btn")

    def on_mount(self) -> None:
        self.query_one("#name", Input).focus()

    @on(Button.Pressed, "#cancel-btn")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#save-btn")
    def _save(self) -> None:
        self._do_save()

    def _do_save(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        ip = self.query_one("#ip", Input).value.strip()
        username = self.query_one("#username", Input).value.strip()
        port_str = self.query_one("#port", Input).value.strip()
        error = self.query_one("#form-error", Static)

        if not name:
            error.update("Name is required")
            return
        if not ip:
            error.update("Host / IP is required")
            return
        if not username:
            error.update("Username is required")
            return
        try:
            port = int(port_str)
        except ValueError:
            error.update("Port must be a number")
            return

        auth_method = self.query_one("#auth_method", Select).value
        key_path = self.query_one("#key_path", Input).value.strip()
        password = self.query_one("#password", Input).value
        tags_raw = self.query_one("#tags", Input).value.strip()
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

        # Jump / tunnel fields
        jump_sel = self.query_one("#proxy_jump", Select).value
        proxy_jump_id = jump_sel if jump_sel else ""

        pf_raw = self.query_one("#port_forwards", Input).value.strip()
        port_forwards = [r.strip() for r in pf_raw.replace(",", "\n").split("\n") if r.strip()] if pf_raw else []

        encrypted_pw = ""
        if auth_method == "password" and password:
            try:
                encrypted_pw = self.app.auth_manager.encrypt(password)
            except Exception:
                error.update("Encryption error")
                return

        if self.connection:
            conn = self.connection
            conn.name = name
            conn.host = ip
            conn.port = port
            conn.username = username
            conn.auth_method = auth_method
            conn.private_key_path = key_path
            conn.tags = tags
            conn.proxy_jump_id = proxy_jump_id
            conn.port_forwards = port_forwards
            if encrypted_pw:
                conn.password_encrypted = encrypted_pw
        else:
            conn = SSHConnection(
                name=name,
                host=ip,
                port=port,
                username=username,
                auth_method=auth_method,
                private_key_path=key_path,
                password_encrypted=encrypted_pw,
                tags=tags,
                proxy_jump_id=proxy_jump_id,
                port_forwards=port_forwards,
            )

        self.dismiss(conn)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
