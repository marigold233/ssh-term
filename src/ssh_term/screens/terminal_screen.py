"""Full-screen SSH terminal."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static
from textual.binding import Binding

from textual import work
from ssh_term.theme import get_color, TERMINAL_BG
from ssh_term.models.connection import SSHConnection
from ssh_term.widgets.terminal_emulator import TerminalEmulator


class TerminalScreen(Screen):
    CSS = """
    TerminalScreen {
        background: """ + TERMINAL_BG + """;
    }
    TerminalScreen #term-status {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+d", "disconnect", "Disconnect", priority=True),
        Binding("ctrl+f", "file_transfer", "File Transfer", priority=True),
    ]

    def __init__(self, connection: SSHConnection, **kwargs) -> None:
        super().__init__(**kwargs)
        self.connection = connection

    def compose(self) -> ComposeResult:
        yield Static("", id="term-status")

    @work
    async def on_mount(self) -> None:
        err = get_color(self.app.theme, "error")
        # Start connection status
        self.query_one("#term-status", Static).update(" Connecting...")
        
        try:
            channel = await self.app.ssh_manager.open_shell(self.connection.id)
            emulator = TerminalEmulator(channel, id="terminal")
            await self.mount(emulator)
            emulator.focus()
        except Exception as e:
            self.query_one("#term-status", Static).update(f" Error: {e}")
            return

        self.query_one("#term-status", Static).update(
            f" {self.connection.name} ({self.connection.host})  |  "
            f"[bold {err}]Ctrl+D[/] disconnect  "
            f"[bold {err}]Ctrl+F[/] files"
        )

    def on_terminal_emulator_disconnected(self, _event) -> None:
        self.app.ssh_manager.disconnect(self.connection.id)
        self.app.pop_screen()
        self.app.notify("Disconnected")

    def action_disconnect(self) -> None:
        terminal = self.query_one("#terminal", TerminalEmulator)
        if terminal:
            terminal.stop()
        self.app.ssh_manager.disconnect(self.connection.id)
        self.app.pop_screen()
        self.app.notify("Disconnected")

    def action_file_transfer(self) -> None:
        from ssh_term.screens.file_transfer import FileTransferScreen
        self.app.push_screen(FileTransferScreen(self.connection))
