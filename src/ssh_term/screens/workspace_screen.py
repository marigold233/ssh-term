"""Multi-tab workspace screen containing multiple SSH terminals."""

from __future__ import annotations

import asyncio
import re

from textual import work, on
from textual.app import ComposeResult
from textual.screen import Screen, ModalScreen
from textual.widgets import Static, TabbedContent, TabPane, Input, Button
from textual.containers import Horizontal, Vertical, Center
from textual.binding import Binding

from ssh_term.theme import get_color, TERMINAL_BG
from ssh_term.models.connection import SSHConnection
from ssh_term.widgets.terminal_emulator import TerminalEmulator
from ssh_term.screens.snippet_palette import SnippetPaletteScreen


# ─────────────────────────────────────────────────────────────────────────────
# Split connection picker modal
# ─────────────────────────────────────────────────────────────────────────────

class SplitConnectionPicker(ModalScreen):
    """Pick which open connection to open in the split pane."""

    BINDINGS = [Binding("escape", "cancel", show=False, priority=True)]

    CSS = """
    SplitConnectionPicker { align: center middle; }
    SplitConnectionPicker #picker-box {
        width: 54;
        height: auto;
        max-height: 22;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    SplitConnectionPicker .picker-title {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
        border-bottom: solid $panel;
    }
    SplitConnectionPicker Button {
        width: 1fr;
        margin-bottom: 1;
    }
    """

    def __init__(self, connections: list[SSHConnection], **kwargs) -> None:
        super().__init__(**kwargs)
        self.connections = connections

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="picker-box"):
                yield Static("F5 — Select connection for split pane", classes="picker-title")
                for conn in self.connections:
                    label = f"  {conn.name}   [{conn.host}:{conn.port}]"
                    yield Button(label, id=f"conn-{conn.id}", variant="default")
                yield Button("Cancel", id="cancel-btn", variant="error")

    def on_mount(self) -> None:
        # Focus first connection button
        try:
            self.query("Button").first().focus()
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
        else:
            conn_id = event.button.id.replace("conn-", "")
            conn = next((c for c in self.connections if c.id == conn_id), None)
            self.dismiss(conn)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ─────────────────────────────────────────────────────────────────────────────
# WorkspaceScreen
# ─────────────────────────────────────────────────────────────────────────────

class WorkspaceScreen(Screen):
    CSS = """
    WorkspaceScreen {
        background: """ + TERMINAL_BG + """;
    }
    WorkspaceScreen TabbedContent {
        height: 1fr;
        overflow: hidden;
    }
    WorkspaceScreen TabPane {
        overflow: hidden;
        padding: 0;
    }
    /* Horizontal container that holds side-by-side split terminals */
    WorkspaceScreen .split-h {
        height: 1fr;
    }
    /* History search bar (docked top, F3 to toggle) */
    WorkspaceScreen #history-search-bar {
        dock: top;
        height: 3;
        background: $surface;
        border-bottom: solid $primary;
        padding: 0 1;
        display: none;
    }
    WorkspaceScreen #history-search-bar .sh-label {
        width: auto;
        padding: 1 1;
        color: $primary;
    }
    WorkspaceScreen #history-search-bar .sh-hint {
        width: auto;
        padding: 1 1;
        color: $text-muted;
    }
    WorkspaceScreen #history-search-input {
        width: 1fr;
    }
    WorkspaceScreen #telemetry-status {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+d", "disconnect_tab", "Close Tab", priority=True),
        Binding("alt+p", "search_snippet", "Snippets", priority=True),
        Binding("ctrl+f", "file_transfer", "Files", priority=True),
        Binding("ctrl+b", "back_to_dash", "Dashboard", priority=True),
        # F3/F5/F7 are also intercepted directly inside TerminalEmulator.on_key
        # so they still work when the terminal has focus.  These fallback bindings
        # fire when the history-search Input (or another widget) has focus.
        Binding("f3", "toggle_history_search", "Search History", priority=True),
        Binding("f5", "split_horizontal", "Split", priority=True),
        Binding("f7", "close_split", "Close Split", priority=True),
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
        self._tabs_data: dict[str, SSHConnection] = {}
        self._telemetry_tasks: dict[str, asyncio.Task] = {}
        self._telemetry_data: dict[str, str] = {}
        self._last_stats = {}
        self._pending_connections = []
        self._scroll_hint: str = ""
        self._session_counter: int = 0
        # Split screen state
        self._split_extras: dict[str, list[str]] = {}  # session_id → [extra term ids]
        self._split_counter: int = 0
        self._focused_term_id: str | None = None
        # History search state
        self._last_search: str = ""

    def on_mount(self) -> None:
        for conn in self._pending_connections:
            self.add_connection_tab(conn)
        self._pending_connections.clear()

    def _next_session_id(self, conn: SSHConnection) -> str:
        self._session_counter += 1
        return f"{conn.id}-{self._session_counter}"

    def enqueue_connection(self, conn: SSHConnection) -> None:
        if self.is_mounted:
            self.add_connection_tab(conn)
        else:
            self._pending_connections.append(conn)

    def compose(self) -> ComposeResult:
        # History search bar overlay (F3 toggles it)
        with Horizontal(id="history-search-bar"):
            yield Static("🔍 Search:", classes="sh-label")
            yield Input(
                placeholder="regex or keyword...  |  Enter = next match  |  Esc = close",
                id="history-search-input",
            )
            yield Static("  F3 / Esc = close", classes="sh-hint")
        with TabbedContent(id="tabs"):
            pass
        yield Static(" Idle", id="telemetry-status")

    # ─────────────────────────────────────────────────────────────────────────
    # Session helpers
    # ─────────────────────────────────────────────────────────────────────────

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

    def _get_active_terminal(self) -> TerminalEmulator | None:
        """Return the currently focused TerminalEmulator (respects splits)."""
        if self._focused_term_id:
            try:
                return self.query_one(f"#{self._focused_term_id}", TerminalEmulator)
            except Exception:
                pass
        session_id = self._get_active_session_id()
        if session_id:
            try:
                return self.query_one(f"#term-{session_id}", TerminalEmulator)
            except Exception:
                pass
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Terminal emulator message handlers (F3/F5/F7 routed from terminal)
    # ─────────────────────────────────────────────────────────────────────────

    def on_terminal_emulator_request_history_search(
        self, event: TerminalEmulator.RequestHistorySearch
    ) -> None:
        self.action_toggle_history_search()

    def on_terminal_emulator_request_split_h(
        self, event: TerminalEmulator.RequestSplitH
    ) -> None:
        self.action_split_horizontal()

    def on_terminal_emulator_request_close_split(
        self, event: TerminalEmulator.RequestCloseSplit
    ) -> None:
        self.action_close_split()

    # ─────────────────────────────────────────────────────────────────────────
    # Tab lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._scroll_hint = ""
        session_id = self._get_active_session_id()
        self._update_telemetry_bar(session_id)
        if session_id:
            self._focused_term_id = f"term-{session_id}"
            try:
                term = self.query_one(f"#term-{session_id}", TerminalEmulator)
                term.focus()
            except Exception:
                pass

    def on_terminal_emulator_scroll_changed(self, event: TerminalEmulator.ScrollChanged) -> None:
        if event.offset > 0:
            self._scroll_hint = f" 📜 HISTORY ↑{event.offset} lines | Shift+End=return"
        else:
            self._scroll_hint = ""
        self._update_telemetry_bar(self._get_active_session_id())

    def _update_telemetry_bar(self, session_id: str | None) -> None:
        try:
            status = self.query_one("#telemetry-status", Static)
        except Exception:
            return
        if not session_id or session_id not in self._tabs_data:
            status.update(" No Active Connection")
            return

        err = get_color(self.app.theme, "error")
        telemetry = self._telemetry_data.get(session_id, "Loading...")
        split_count = len(self._split_extras.get(session_id, []))
        split_hint = f"  [bold {err}]F7[/] Close Split" if split_count else ""

        if self._scroll_hint:
            status.update(
                f"[bold yellow]{self._scroll_hint}[/] | "
                f"[bold {err}]Ctrl+D[/] Close  [bold {err}]Ctrl+B[/] Dash"
            )
        else:
            status.update(
                f" {telemetry} | "
                f"[bold {err}]Ctrl+D[/] Close  [bold {err}]Alt+P[/] Snippets  "
                f"[bold {err}]Ctrl+F[/] Files  [bold {err}]F3[/] Search  "
                f"[bold {err}]F5[/] Split{split_hint}  [bold {err}]Ctrl+B[/] Dash"
            )

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
        self._split_extras[session_id] = []

        try:
            channel = await self.app.ssh_manager.open_shell(connection.id)
            emulator = TerminalEmulator(channel, id=f"term-{session_id}")
            # Wrap in Horizontal so F5 can add side-by-side split terminals
            container = Horizontal(emulator, id=f"split-{session_id}", classes="split-h")
            await pane.mount(container)
            emulator.focus()
            self._focused_term_id = f"term-{session_id}"
            self._start_telemetry(session_id, connection)
        except Exception as e:
            await pane.mount(Static(f"Failed to connect: {e}"))

        self._update_telemetry_bar(session_id)

    # ─────────────────────────────────────────────────────────────────────────
    # Split screen  (F5 = split, F7 = close splits)
    # ─────────────────────────────────────────────────────────────────────────

    @work
    async def action_split_horizontal(self) -> None:
        """Open a new shell in a split pane beside the active terminal (F5).

        If multiple connections are already open, shows a picker so the user
        can choose which server to open in the split.
        """
        session_id = self._get_active_session_id()
        if not session_id or session_id not in self._tabs_data:
            return

        try:
            container = self.query_one(f"#split-{session_id}", Horizontal)
        except Exception:
            self.notify("Cannot split this pane", severity="warning")
            return

        # Gather unique active connections
        seen: dict[str, SSHConnection] = {}
        for conn in self._tabs_data.values():
            seen[conn.id] = conn
        unique_conns = list(seen.values())

        if len(unique_conns) > 1:
            # Show connection picker
            chosen: SSHConnection | None = await self.app.push_screen_wait(
                SplitConnectionPicker(unique_conns)
            )
            if chosen is None:
                return  # User cancelled
            conn = chosen
        else:
            # Only one active connection — split same server
            conn = self._tabs_data[session_id]

        self._split_counter += 1
        extra_id = f"term-extra-{self._split_counter}"

        try:
            channel = await self.app.ssh_manager.open_shell(conn.id)
            emulator = TerminalEmulator(channel, id=extra_id)
            await container.mount(emulator)
            emulator.focus()
            self._focused_term_id = extra_id
            self._split_extras[session_id].append(extra_id)
            self._update_telemetry_bar(session_id)
            self.notify("Split opened  —  F7 to close", timeout=2)
        except Exception as e:
            self.notify(f"Split failed: {e}", severity="error")

    def action_close_split(self) -> None:
        """Remove all split panes, keep only the primary terminal (F7)."""
        session_id = self._get_active_session_id()
        if not session_id:
            return
        extras = self._split_extras.pop(session_id, [])
        for term_id in extras:
            try:
                term = self.query_one(f"#{term_id}", TerminalEmulator)
                term.stop()
                term.remove()
            except Exception:
                pass
        self._split_extras[session_id] = []
        self._focused_term_id = f"term-{session_id}"
        try:
            self.query_one(f"#term-{session_id}", TerminalEmulator).focus()
        except Exception:
            pass
        self._update_telemetry_bar(session_id)

    # ─────────────────────────────────────────────────────────────────────────
    # History search  (F3 = toggle)
    # ─────────────────────────────────────────────────────────────────────────

    def action_toggle_history_search(self) -> None:
        """Show / hide the history search bar (F3)."""
        try:
            bar = self.query_one("#history-search-bar", Horizontal)
        except Exception:
            return
        if bar.display:
            bar.display = False
            term = self._get_active_terminal()
            if term:
                term.focus()
        else:
            bar.display = True
            try:
                inp = self.query_one("#history-search-input", Input)
                inp.value = ""
                inp.focus()
            except Exception:
                pass

    @on(Input.Submitted, "#history-search-input")
    def _on_search_submit(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self._last_search = query
        self._do_history_search(self._last_search)

    def _do_history_search(self, query: str) -> None:
        """Scan terminal history backward from the current view for a regex match."""
        if not query:
            return
        term = self._get_active_terminal()
        if not term:
            return
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error:
            self.notify(f"Invalid regex: {query}", severity="warning")
            return

        total = term._pyte_screen.get_total_lines()
        start = term._scroll_offset + 1  # start searching above current view

        for offset in range(start, total):
            abs_y = max(0, total - term._rows - offset)
            segments = term._pyte_screen.get_line_segments(abs_y)
            line_text = "".join(seg[0] for seg in segments).strip()
            if line_text and pattern.search(line_text):
                term._set_scroll_offset(offset)
                self.notify(f"Match  ↑{offset} lines  (Enter = next)", timeout=1.5)
                return

        self.notify("No more matches", severity="warning", timeout=1.5)

    def on_key(self, event) -> None:
        """Handle Esc to close history search bar (bubbles up from Input)."""
        if event.key == "escape":
            try:
                bar = self.query_one("#history-search-bar", Horizontal)
                if bar.display:
                    bar.display = False
                    term = self._get_active_terminal()
                    if term:
                        term.focus()
                    event.stop()
            except Exception:
                pass

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
                if self._get_active_session_id() == session_id:
                    try:
                        res = await client.run(cmd)
                        out = res.stdout if hasattr(res, "stdout") else ""
                        parsed = self._parse_telemetry(session_id, str(out))
                        self._telemetry_data[session_id] = parsed
                        self._update_telemetry_bar(session_id)
                    except asyncio.CancelledError:
                        return
                    except Exception as e:
                        self._telemetry_data[session_id] = f"Telemetry Error: {type(e).__name__}"
                        self._update_telemetry_bar(session_id)
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
                    total_j = user + nice + system + idle + iowait
                    idle_t = idle + iowait
                    s = self._last_stats.setdefault(session_id, {})
                    if "last_total" in s:
                        dt = total_j - s["last_total"]
                        di = idle_t - s["last_idle"]
                        if dt > 0:
                            cpu_usage = (dt - di) / dt * 100
                    s["last_total"] = total_j
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
        stats = self._last_stats.setdefault(session_id, {})
        if "last_rx" in stats and "last_ts" in stats:
            dt = now - stats["last_ts"]
            if dt > 0:
                rx_rate_str = fmt_rate((rx_bytes - stats["last_rx"]) / dt)
                tx_rate_str = fmt_rate((tx_bytes - stats["last_tx"]) / dt)
        stats["last_rx"] = rx_bytes
        stats["last_tx"] = tx_bytes
        stats["last_ts"] = now

        return (
            f"CPU: {cpu_usage:.1f}% | Mem: {mem_pct:.1f}% | "
            f"Disk: {disk_str} | ↓ {rx_rate_str} ↑ {tx_rate_str}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Disconnect / close
    # ─────────────────────────────────────────────────────────────────────────

    def on_terminal_emulator_disconnected(self, event) -> None:
        session_id = (
            event.term_id.replace("term-", "")
            if getattr(event, "term_id", None)
            else None
        )
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

        # Stop all split terminals
        for term_id in self._split_extras.pop(session_id, []):
            try:
                t = self.query_one(f"#{term_id}", TerminalEmulator)
                t.stop()
                t.remove()
            except Exception:
                pass

        # Stop the primary terminal
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
                    term.write_stdin(cleaned)
                except Exception as e:
                    self.notify(f"Failed to inject snippet: {e}", severity="error")

    def action_back_to_dash(self) -> None:
        self.app.switch_screen("dashboard")

    # ─────────────────────────────────────────────────────────────────────────
    # History scrolling
    # ─────────────────────────────────────────────────────────────────────────

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
