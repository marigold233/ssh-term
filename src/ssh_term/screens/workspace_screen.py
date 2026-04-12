"""Multi-tab workspace screen: 3-column layout with split-screen, broadcast, sidebar & history search."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual import work, on
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, TabbedContent, TabPane, Input, DirectoryTree
from textual.containers import Horizontal, Vertical
from textual.binding import Binding
from textual.message import Message

from ssh_term.theme import get_color, TERMINAL_BG
from ssh_term.models.connection import SSHConnection
from ssh_term.widgets.terminal_emulator import TerminalEmulator
from ssh_term.widgets.remote_file_tree import RemoteFileTree


# ─────────────────────────────────────────────────────────────────────────────
# Sub-widgets
# ─────────────────────────────────────────────────────────────────────────────

class SysInfoPanel(Static):
    """Displays live system telemetry for the active session."""
    DEFAULT_CSS = """
    SysInfoPanel {
        width: 1fr;
        height: auto;
        min-height: 8;
        border: solid $panel;
        padding: 0 1;
        color: $text-muted;
    }
    SysInfoPanel .sysinfo-title {
        text-style: bold;
        color: $primary;
    }
    """

    def render_info(self, conn: SSHConnection | None, telemetry: str) -> str:
        if not conn:
            return "[dim]No active connection[/]"
        pf_active = ""
        try:
            pf_active = self._pf_summary(conn)
        except Exception:
            pass
        return (
            f"[bold $primary][ {conn.name} ][/]\n"
            f"Host: {conn.host}:{conn.port}\n"
            f"User: {conn.username}\n"
            f"Tags: {', '.join(conn.tags) if conn.tags else '—'}\n"
            f"{pf_active}"
            f"{telemetry}"
        )

    def _pf_summary(self, conn: SSHConnection) -> str:
        if not conn.port_forwards:
            return ""
        lines = "\n".join(f"  ⇢ 127.0.0.1:{r.split(':')[0]} → {':'.join(r.split(':')[1:])}"
                          for r in conn.port_forwards)
        return f"Tunnels:\n{lines}\n"


class QuickCmdPanel(Static):
    """Quick command launcher - most-used snippet shortcuts."""
    DEFAULT_CSS = """
    QuickCmdPanel {
        width: 1fr;
        height: 1fr;
        border: solid $panel;
        padding: 0 1;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("[bold $secondary]Quick Commands[/]")
        yield Input(placeholder="> type command...", id="quick-cmd-input")

    def on_mount(self) -> None:
        # Will be populated dynamically from snippets
        pass


class HistorySearchOverlay(Static):
    """Floating history search bar."""
    DEFAULT_CSS = """
    HistorySearchOverlay {
        dock: top;
        height: 3;
        background: $surface;
        border-bottom: solid $primary;
        padding: 0 1;
        display: none;
    }
    HistorySearchOverlay Horizontal {
        height: 3;
    }
    HistorySearchOverlay Input {
        width: 1fr;
    }
    HistorySearchOverlay Static {
        width: auto;
        padding: 1 1;
        color: $text-muted;
    }
    """

    class Search(Message):
        def __init__(self, query: str) -> None:
            super().__init__()
            self.query = query

    class Close(Message):
        pass

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("🔍 Search: ")
            yield Input(placeholder="regex or keyword...", id="history-search-input")
            yield Static("  [dim]Enter=jump  Esc=close[/]")

    def open(self) -> None:
        self.display = True
        try:
            self.query_one("#history-search-input", Input).focus()
        except Exception:
            pass

    def close(self) -> None:
        self.display = False

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "history-search-input":
            self.post_message(self.Search(event.value.strip()))

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.post_message(self.Close())
            event.stop()


# ─────────────────────────────────────────────────────────────────────────────
# WorkspaceScreen
# ─────────────────────────────────────────────────────────────────────────────

class WorkspaceScreen(Screen):
    CSS = """
    WorkspaceScreen {
        background: """ + TERMINAL_BG + """;
    }
    WorkspaceScreen #workspace-layout {
        height: 1fr;
    }
    /* ── Left Sidebar ───────────────────────────────────────── */
    WorkspaceScreen #left-sidebar {
        width: 28;
        min-width: 28;
        height: 1fr;
        background: $background;
        border-right: solid $panel;
    }
    WorkspaceScreen #sys-info {
        height: auto;
        min-height: 9;
        max-height: 14;
        padding: 0 1;
        color: $text-muted;
        border-bottom: solid $panel;
    }
    WorkspaceScreen #quick-cmd-panel {
        height: 1fr;
        padding: 0 1;
    }
    WorkspaceScreen #quick-cmd-label {
        text-style: bold;
        color: $secondary;
        padding: 0 0;
        margin-bottom: 0;
    }
    WorkspaceScreen #quick-cmd-input {
        width: 1fr;
    }
    /* ── SFTP Sidebar ───────────────────────────────────────── */
    WorkspaceScreen #sftp-sidebar {
        width: 28;
        min-width: 28;
        height: 1fr;
        background: $background;
        border-left: solid $panel;
    }
    WorkspaceScreen #sftp-sidebar-label {
        height: 1;
        text-style: bold;
        color: $accent;
        background: $panel;
        padding: 0 1;
    }
    WorkspaceScreen #sftp-remote-tree {
        height: 1fr;
    }
    WorkspaceScreen #sftp-placeholder {
        padding: 1;
        color: $text-muted;
    }
    /* ── Main terminal area ─────────────────────────────────── */
    WorkspaceScreen #main-content {
        width: 1fr;
        height: 1fr;
    }
    WorkspaceScreen #tabs {
        height: 1fr;
        overflow: hidden;
    }
    WorkspaceScreen TabPane {
        overflow: hidden;
        padding: 0;
    }
    WorkspaceScreen .term-grid {
        height: 1fr;
    }
    WorkspaceScreen .term-split-h {
        height: 1fr;
    }
    WorkspaceScreen .term-split-v {
        width: 1fr;
        height: 1fr;
    }
    /* ── Status bar ─────────────────────────────────────────── */
    WorkspaceScreen #telemetry-status {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    WorkspaceScreen #broadcast-indicator {
        dock: bottom;
        height: 1;
        background: $error;
        color: $background;
        text-style: bold;
        content-align: center middle;
        display: none;
    }
    """

    BINDINGS = [
        Binding("ctrl+d", "disconnect_tab", "Close Tab", priority=True),
        Binding("alt+p", "search_snippet", "Snippets", priority=True),
        Binding("ctrl+f", "file_transfer", "Files", priority=True),
        Binding("ctrl+b", "back_to_dash", "Dashboard", priority=True),
        Binding("f4", "toggle_broadcast", "Broadcast", priority=True),
        Binding("f3", "toggle_search", "Search History", priority=True),
        Binding("ctrl+s", "split_horizontal", "Split H", priority=True),
        Binding("ctrl+left_square_bracket", "toggle_left_sidebar", "Left Panel", priority=True),
        Binding("ctrl+right_square_bracket", "toggle_right_sidebar", "Right Panel", priority=True),
        Binding("shift+page_up", "scroll_history_up", "Scroll Up", show=False, priority=True),
        Binding("shift+pageup", "scroll_history_up", "Scroll Up", show=False, priority=True),
        Binding("ctrl+up", "scroll_history_up", "Scroll Up", show=False, priority=True),
        Binding("shift+page_down", "scroll_history_down", "Scroll Down", show=False, priority=True),
        Binding("shift+pagedown", "scroll_history_down", "Scroll Down", show=False, priority=True),
        Binding("ctrl+down", "scroll_history_down", "Scroll Down", show=False, priority=True),
        Binding("shift+home", "scroll_history_top", "History Top", show=False, priority=True),
        Binding("ctrl+home", "scroll_history_top", "History Top", show=False, priority=True),
        Binding("shift+end", "scroll_history_bottom", "History Bottom", show=False, priority=True),
        Binding("ctrl+end", "scroll_history_bottom", "History Bottom", show=False, priority=True),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._tabs_data: dict[str, SSHConnection] = {}       # session_id -> conn
        self._telemetry_tasks: dict[str, asyncio.Task] = {}
        self._telemetry_data: dict[str, str] = {}
        self._last_stats: dict = {}
        self._pending_connections: list[SSHConnection] = []
        self._scroll_hint: str = ""
        self._session_counter: int = 0
        self._broadcast_mode: bool = False                    # F4 toggle
        self._sftp_manager = None
        self._sftp_conn_id: str | None = None                 # which conn the SFTP panel is attached to

    # ─────────────────────────────────────────────────────────────────────────
    # Compose & Mount
    # ─────────────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield HistorySearchOverlay(id="history-search-overlay")
        with Horizontal(id="workspace-layout"):
            # ── Left Sidebar ───────────────────────────────
            with Vertical(id="left-sidebar"):
                yield Static(" 系统详情", id="sys-info")
                with Vertical(id="quick-cmd-panel"):
                    yield Static("⚡ Quick Command", id="quick-cmd-label")
                    yield Input(placeholder="> send to active terminal...", id="quick-cmd-input")
            # ── Main (tabs) ────────────────────────────────
            with Vertical(id="main-content"):
                with TabbedContent(id="tabs"):
                    pass
        # Broadcast indicator (shown above telemetry when active)
        yield Static(
            "📡 BROADCAST MODE ON — All terminals receive input  [bold]F4[/] to disable",
            id="broadcast-indicator",
        )
        # ── SFTP Right Sidebar  ────────────────────────────
        with Vertical(id="sftp-sidebar"):
            yield Static("📁 Remote Files", id="sftp-sidebar-label")
            yield Static("Connect to a server\nto browse files.", id="sftp-placeholder")
        # Status bar (docked bottom)
        yield Static(" Idle", id="telemetry-status")

    def on_mount(self) -> None:
        for conn in self._pending_connections:
            self.add_connection_tab(conn)
        self._pending_connections.clear()
        # Wire the quick-command input
        try:
            self.query_one("#quick-cmd-input", Input).focus()
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Quick Command input
    # ─────────────────────────────────────────────────────────────────────────

    @on(Input.Submitted, "#quick-cmd-input")
    def _on_quick_cmd(self, event: Input.Submitted) -> None:
        cmd = event.value.strip()
        if not cmd:
            return
        if self._broadcast_mode:
            # Send to ALL terminals
            for sid in self._tabs_data:
                try:
                    term = self.query_one(f"#term-{sid}", TerminalEmulator)
                    term.write_stdin(cmd + "\r")
                except Exception:
                    pass
        else:
            term = self._get_active_terminal()
            if term:
                term.write_stdin(cmd + "\r")
        event.input.value = ""
        # Re-focus the active terminal
        term = self._get_active_terminal()
        if term:
            term.focus()

    # ─────────────────────────────────────────────────────────────────────────
    # Session helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _next_session_id(self, conn: SSHConnection) -> str:
        self._session_counter += 1
        return f"{conn.id}-{self._session_counter}"

    def enqueue_connection(self, conn: SSHConnection) -> None:
        if self.is_mounted:
            self.add_connection_tab(conn)
        else:
            self._pending_connections.append(conn)

    def _get_active_session_id(self) -> str | None:
        tabs = self.query("TabbedContent")
        if tabs:
            tc = tabs.first()
            if tc.active:
                return tc.active.replace("tab-", "")
        return None

    def get_active_connection_id(self) -> str | None:
        session_id = self._get_active_session_id()
        if session_id and session_id in self._tabs_data:
            return self._tabs_data[session_id].id
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Tab activation
    # ─────────────────────────────────────────────────────────────────────────

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._scroll_hint = ""
        session_id = self._get_active_session_id()
        self._update_telemetry_bar(session_id)
        if session_id:
            self._update_sys_info(session_id)
            try:
                term = self.query_one(f"#term-{session_id}", TerminalEmulator)
                term.focus()
            except Exception:
                pass
            # Attach SFTP panel to the new active connection
            conn = self._tabs_data.get(session_id)
            if conn:
                self._attach_sftp_panel(conn)

    def _update_sys_info(self, session_id: str | None) -> None:
        try:
            panel = self.query_one("#sys-info", Static)
        except Exception:
            return
        if not session_id or session_id not in self._tabs_data:
            panel.update(" 系统详情\n[dim]No active connection[/]")
            return
        conn = self._tabs_data[session_id]
        telemetry = self._telemetry_data.get(session_id, "Loading…")
        # Port forward summary
        pf_lines = ""
        if conn.port_forwards:
            pf_lines = "\n".join(
                f"  ⇢ 127.0.0.1:{r.split(':')[0]}→{':'.join(r.split(':')[1:])}"
                for r in conn.port_forwards
            )
            pf_lines = f"\n[dim]Tunnels:[/]\n{pf_lines}"
        jump_label = ""
        if conn.proxy_jump_id:
            jconn = next(
                (c for c in self.app.config_manager.connections if c.id == conn.proxy_jump_id),
                None,
            )
            jump_label = f"\n[dim]Via:[/] {jconn.name if jconn else conn.proxy_jump_id}"
        panel.update(
            f"[bold][ {conn.name} ][/]\n"
            f"[dim]Host:[/] {conn.host}:{conn.port}\n"
            f"[dim]User:[/] {conn.username}\n"
            f"[dim]Tags:[/] {', '.join(conn.tags) if conn.tags else '—'}"
            f"{jump_label}"
            f"{pf_lines}\n"
            f"[dim]─────────────────────[/]\n"
            f"{telemetry}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # SFTP side panel
    # ─────────────────────────────────────────────────────────────────────────

    def _attach_sftp_panel(self, conn: SSHConnection) -> None:
        if self._sftp_conn_id == conn.id:
            return  # Already attached
        self._sftp_conn_id = conn.id
        self._init_sftp_panel(conn)

    @work
    async def _init_sftp_panel(self, conn: SSHConnection) -> None:
        try:
            sftp_raw = await self.app.ssh_manager.open_sftp(conn.id)
            from ssh_term.services.sftp_manager import SFTPManager
            self._sftp_manager = SFTPManager(sftp_raw)
            cwd = await self._sftp_manager.cwd()
        except Exception as e:
            try:
                self.query_one("#sftp-placeholder", Static).update(f"SFTP unavailable:\n{e}")
            except Exception:
                pass
            return

        try:
            # Remove old remote tree if present
            for old in self.query("RemoteFileTree"):
                old.remove()
            ph = self.query_one("#sftp-placeholder", Static)
            ph.display = False
        except Exception:
            pass

        sidebar = self.query_one("#sftp-sidebar")
        tree = RemoteFileTree(self._sftp_manager, cwd, id="sftp-remote-tree")
        await sidebar.mount(tree)

    # ─────────────────────────────────────────────────────────────────────────
    # Telemetry status bar
    # ─────────────────────────────────────────────────────────────────────────

    def on_terminal_emulator_scroll_changed(self, event: TerminalEmulator.ScrollChanged) -> None:
        if event.offset > 0:
            self._scroll_hint = f" 📜 HISTORY ↑{event.offset} lines | Shift+End=return"
        else:
            self._scroll_hint = ""
        session_id = self._get_active_session_id()
        self._update_telemetry_bar(session_id)

    def _update_telemetry_bar(self, session_id: str | None) -> None:
        try:
            status = self.query_one("#telemetry-status", Static)
        except Exception:
            return
        err = get_color(self.app.theme, "error")
        bcast = " [bold red]📡BROADCAST[/] |" if self._broadcast_mode else ""
        if self._scroll_hint:
            status.update(
                f"[bold yellow]{self._scroll_hint}[/] |"
                f"{bcast} [bold {err}]F3[/] Search  [bold {err}]Ctrl+D[/] Close  [bold {err}]Ctrl+B[/] Dash"
            )
        elif session_id and session_id in self._tabs_data:
            telemetry = self._telemetry_data.get(session_id, "")
            status.update(
                f" {telemetry} |"
                f"{bcast} [bold {err}]Ctrl+\\[[/] Sidebar  [bold {err}]F4[/] Broadcast  "
                f"[bold {err}]Alt+P[/] Snippets  [bold {err}]Ctrl+F[/] Files  [bold {err}]Ctrl+B[/] Dash"
            )
        else:
            status.update(f" No Active Connection |{bcast}")

    # ─────────────────────────────────────────────────────────────────────────
    # Tab creation
    # ─────────────────────────────────────────────────────────────────────────

    @work
    async def add_connection_tab(self, connection: SSHConnection) -> None:
        session_id = self._next_session_id(connection)
        tab_id = f"tab-{session_id}"
        tabs = self.query_one("#tabs", TabbedContent)

        count = sum(1 for c in self._tabs_data.values() if c.id == connection.id)
        tab_label = connection.name if count == 0 else f"{connection.name} ({count + 1})"

        pane = TabPane(tab_label, id=tab_id)
        await tabs.add_pane(pane)
        tabs.active = tab_id
        self._tabs_data[session_id] = connection

        try:
            channel = await self.app.ssh_manager.open_shell(connection.id)
            emulator = TerminalEmulator(channel, id=f"term-{session_id}")
            # Wrap in a split-capable container
            grid = Horizontal(emulator, classes="term-split-h", id=f"grid-{session_id}")
            await pane.mount(grid)
            emulator.focus()
            self._start_telemetry(session_id, connection)
            self._attach_sftp_panel(connection)
        except Exception as e:
            await pane.mount(Static(f"[red]Failed to connect:[/] {e}"))

        self._update_telemetry_bar(session_id)

    # ─────────────────────────────────────────────────────────────────────────
    # Split-screen
    # ─────────────────────────────────────────────────────────────────────────

    @work
    async def action_split_horizontal(self) -> None:
        """Add a second shell for the same server side-by-side."""
        session_id = self._get_active_session_id()
        if not session_id or session_id not in self._tabs_data:
            return
        conn = self._tabs_data[session_id]
        try:
            grid = self.query_one(f"#grid-{session_id}", Horizontal)
        except Exception:
            return

        split_id = f"split-{self._session_counter}"
        self._session_counter += 1
        try:
            channel = await self.app.ssh_manager.open_shell(conn.id)
            emulator = TerminalEmulator(channel, id=split_id)
            await grid.mount(emulator)
            emulator.focus()
            self.notify("Split added", timeout=1.5)
        except Exception as e:
            self.notify(f"Split failed: {e}", severity="error")

    # ─────────────────────────────────────────────────────────────────────────
    # Broadcast mode (F4)
    # ─────────────────────────────────────────────────────────────────────────

    def action_toggle_broadcast(self) -> None:
        self._broadcast_mode = not self._broadcast_mode
        try:
            indicator = self.query_one("#broadcast-indicator", Static)
            indicator.display = self._broadcast_mode
        except Exception:
            pass
        state = "ON 📡" if self._broadcast_mode else "OFF"
        self.notify(f"Broadcast mode {state}", timeout=2)
        session_id = self._get_active_session_id()
        self._update_telemetry_bar(session_id)

    def on_terminal_emulator_key_broadcast(self, event: TerminalEmulator.KeyBroadcast) -> None:
        """Fan-out keystrokes to all other terminals when broadcast mode is active."""
        if not self._broadcast_mode:
            return
        # Find which terminal emitted this to skip it
        sender_id = event.control.id if event.control else None
        for sid in self._tabs_data:
            term_id = f"term-{sid}"
            if term_id == sender_id:
                continue  # Skip the originating terminal
            try:
                term = self.query_one(f"#{term_id}", TerminalEmulator)
                term.write_stdin(event.data)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # History Search (F3 / /)
    # ─────────────────────────────────────────────────────────────────────────

    def action_toggle_search(self) -> None:
        overlay = self.query_one("#history-search-overlay", HistorySearchOverlay)
        if overlay.display:
            overlay.close()
            term = self._get_active_terminal()
            if term:
                term.focus()
        else:
            overlay.open()

    def on_history_search_overlay_search(self, event: HistorySearchOverlay.Search) -> None:
        term = self._get_active_terminal()
        if not term or not event.query:
            return
        # Search through history buffer by scanning rendered lines for a match
        import re
        try:
            pattern = re.compile(event.query, re.IGNORECASE)
        except re.error:
            self.notify("Invalid regex", severity="warning")
            return

        total = term._pyte_screen.get_total_lines()
        # Scan from current view upward for first match
        for offset in range(term._scroll_offset + 1, total):
            abs_y = max(0, total - term._rows - offset)
            segments = term._pyte_screen.get_line_segments(abs_y)
            line_text = "".join(seg[0] for seg in segments)
            if pattern.search(line_text):
                term._set_scroll_offset(offset)
                self.notify(f"Found at -{offset} lines", timeout=1.5)
                return
        self.notify("No more matches", severity="warning")

    def on_history_search_overlay_close(self, event: HistorySearchOverlay.Close) -> None:
        self.query_one("#history-search-overlay", HistorySearchOverlay).close()
        term = self._get_active_terminal()
        if term:
            term.focus()

    # ─────────────────────────────────────────────────────────────────────────
    # Sidebar toggles
    # ─────────────────────────────────────────────────────────────────────────

    def action_toggle_left_sidebar(self) -> None:
        sidebar = self.query_one("#left-sidebar")
        sidebar.display = not sidebar.display

    def action_toggle_right_sidebar(self) -> None:
        sidebar = self.query_one("#sftp-sidebar")
        sidebar.display = not sidebar.display

    # ─────────────────────────────────────────────────────────────────────────
    # Telemetry background task
    # ─────────────────────────────────────────────────────────────────────────

    def _start_telemetry(self, session_id: str, conn: SSHConnection) -> None:
        async def loop():
            client = self.app.ssh_manager.get_client(conn.id)
            if not client:
                return
            cmd = "cat /proc/stat /proc/meminfo /proc/net/dev; echo '---'; df -B1 /"
            while session_id in self._tabs_data:
                try:
                    res = await client.run(cmd)
                    out = res.stdout if hasattr(res, "stdout") else ""
                    parsed = self._parse_telemetry(session_id, str(out))
                    self._telemetry_data[session_id] = parsed
                    if self._get_active_session_id() == session_id:
                        self._update_telemetry_bar(session_id)
                        self._update_sys_info(session_id)
                except asyncio.CancelledError:
                    return
                except Exception:
                    pass
                await asyncio.sleep(3)

        task = asyncio.create_task(loop())
        self._telemetry_tasks[session_id] = task

    def _parse_telemetry(self, session_id: str, raw: str) -> str:
        import time
        now = time.time()
        lines = raw.split("\n")
        cpu_usage = 0.0
        mem_total = mem_avail = rx_bytes = tx_bytes = 0
        disk_str = ""

        for idx, line in enumerate(lines):
            line = line.strip()
            if line.startswith("cpu "):
                parts = line.split()
                if len(parts) > 5:
                    try:
                        user, nice, system, idle, iowait = map(int, parts[1:6])
                    except ValueError:
                        continue
                    total = user + nice + system + idle + iowait
                    idle_t = idle + iowait
                    s = self._last_stats.setdefault(session_id, {})
                    if "last_total" in s:
                        dt = total - s["last_total"]
                        di = idle_t - s["last_idle"]
                        if dt > 0:
                            cpu_usage = (dt - di) / dt * 100
                    s["last_total"] = total
                    s["last_idle"] = idle_t
            elif line.startswith("MemTotal:"):
                try:
                    mem_total = int(line.split()[1])
                except (ValueError, IndexError):
                    pass
            elif line.startswith("MemAvailable:"):
                try:
                    mem_avail = int(line.split()[1])
                except (ValueError, IndexError):
                    pass
            elif ":" in line and any(k in line for k in ("eth", "ens", "enp", "wlan")):
                parts = line.split(":")
                if len(parts) == 2:
                    nums = parts[1].split()
                    if len(nums) >= 9:
                        try:
                            rx_bytes += int(nums[0])
                            tx_bytes += int(nums[8])
                        except ValueError:
                            pass
            elif line.startswith("---"):
                if idx + 2 < len(lines):
                    df_parts = lines[idx + 2].split()
                    if len(df_parts) > 4:
                        disk_str = df_parts[4]

        mem_pct = (mem_total - mem_avail) / mem_total * 100 if mem_total else 0.0

        def fmt_rate(b: float) -> str:
            if b < 0:
                return "0KB/s"
            if b > 1024 * 1024:
                return f"{b/1024/1024:.1f}MB/s"
            return f"{b/1024:.1f}KB/s"

        rx_rate_str = tx_rate_str = "0KB/s"
        s = self._last_stats.setdefault(session_id, {})
        if "last_rx" in s and "last_ts" in s:
            dt = now - s["last_ts"]
            if dt > 0:
                rx_rate_str = fmt_rate((rx_bytes - s["last_rx"]) / dt)
                tx_rate_str = fmt_rate((tx_bytes - s["last_tx"]) / dt)
        s["last_rx"] = rx_bytes
        s["last_tx"] = tx_bytes
        s["last_ts"] = now

        return f"CPU {cpu_usage:.0f}% | Mem {mem_pct:.0f}% | Disk {disk_str} | ↓{rx_rate_str} ↑{tx_rate_str}"

    # ─────────────────────────────────────────────────────────────────────────
    # Disconnection / cleanup
    # ─────────────────────────────────────────────────────────────────────────

    def on_terminal_emulator_disconnected(self, event) -> None:
        session_id = event.term_id.replace("term-", "") if getattr(event, "term_id", None) else None
        if session_id:
            self._close_session(session_id)

    def on_screen_resume(self, event) -> None:
        term = self._get_active_terminal()
        if term:
            term.refresh()
            term.focus()

    def action_disconnect_tab(self, _conn_id: str = None) -> None:
        session_id = self._get_active_session_id()
        if session_id:
            self._close_session(session_id)

    def _close_session(self, session_id: str) -> None:
        if session_id not in self._tabs_data:
            return
        conn = self._tabs_data[session_id]

        if session_id in self._telemetry_tasks:
            self._telemetry_tasks[session_id].cancel()
            del self._telemetry_tasks[session_id]

        try:
            term = self.query_one(f"#term-{session_id}", TerminalEmulator)
            term.stop()
        except Exception:
            pass

        del self._tabs_data[session_id]
        self._telemetry_data.pop(session_id, None)
        self._last_stats.pop(session_id, None)

        other = [sid for sid, c in self._tabs_data.items() if c.id == conn.id]
        if not other:
            self.app.ssh_manager.disconnect(conn.id)

        tabs = self.query_one("#tabs", TabbedContent)
        try:
            tabs.remove_pane(f"tab-{session_id}")
        except Exception:
            pass

        if not self._tabs_data:
            self.app.switch_screen("dashboard")
            self.app.notify("All tabs closed")
        else:
            self.app.notify("Tab closed")

    # ─────────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────────

    def action_file_transfer(self) -> None:
        conn_id = self.get_active_connection_id()
        if conn_id:
            conn = self._tabs_data.get(self._get_active_session_id())
            if conn:
                from ssh_term.screens.file_transfer import FileTransferScreen
                self.app.push_screen(FileTransferScreen(conn))

    @work
    async def action_search_snippet(self) -> None:
        from ssh_term.screens.snippet_palette import SnippetPaletteScreen
        content = await self.app.push_screen_wait(SnippetPaletteScreen(self.app.config_manager))
        if content:
            tc = self.query_one("#tabs", TabbedContent)
            session_id = tc.active.replace("tab-", "")
            if session_id:
                try:
                    cleaned = content.rstrip("\r\n")
                    term = self.query_one(f"#term-{session_id}", TerminalEmulator)
                    if self._broadcast_mode:
                        for sid in self._tabs_data:
                            try:
                                self.query_one(f"#term-{sid}", TerminalEmulator).write_stdin(cleaned)
                            except Exception:
                                pass
                    else:
                        term.write_stdin(cleaned)
                except Exception as e:
                    self.notify(f"Failed to inject snippet: {e}", severity="error")

    def action_back_to_dash(self) -> None:
        self.app.switch_screen("dashboard")

    # ─────────────────────────────────────────────────────────────────────────
    # History scrolling helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _get_active_terminal(self) -> TerminalEmulator | None:
        session_id = self._get_active_session_id()
        if session_id:
            try:
                return self.query_one(f"#term-{session_id}", TerminalEmulator)
            except Exception:
                pass
        return None

    def action_scroll_history_up(self) -> None:
        term = self._get_active_terminal()
        if term:
            term._set_scroll_offset(term._scroll_offset + term._rows)

    def action_scroll_history_down(self) -> None:
        term = self._get_active_terminal()
        if term:
            term._set_scroll_offset(term._scroll_offset - term._rows)

    def action_scroll_history_top(self) -> None:
        term = self._get_active_terminal()
        if term:
            term._set_scroll_offset(term._max_scroll_offset)

    def action_scroll_history_bottom(self) -> None:
        term = self._get_active_terminal()
        if term:
            term._set_scroll_offset(0)

    def on_mouse_scroll_up(self, event) -> None:
        term = self._get_active_terminal()
        if term:
            event.stop()
            term._set_scroll_offset(term._scroll_offset + 3)

    def on_mouse_scroll_down(self, event) -> None:
        term = self._get_active_terminal()
        if term:
            event.stop()
            term._set_scroll_offset(term._scroll_offset - 3)
