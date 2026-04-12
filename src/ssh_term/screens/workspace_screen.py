"""Multi-tab workspace screen containing multiple SSH terminals."""

from __future__ import annotations

import asyncio
from textual import work
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, TabbedContent, TabPane
from textual.binding import Binding

from ssh_term.theme import get_color, TERMINAL_BG
from ssh_term.models.connection import SSHConnection
from ssh_term.widgets.terminal_emulator import TerminalEmulator
from ssh_term.screens.snippet_palette import SnippetPaletteScreen

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
        # session_id -> SSHConnection  (session_id = "{conn.id}-{seq}")
        self._tabs_data: dict[str, SSHConnection] = {}
        self._telemetry_tasks: dict[str, asyncio.Task] = {}
        self._telemetry_data: dict[str, str] = {}
        self._last_stats = {}
        self._pending_connections = []
        self._scroll_hint: str = ""
        self._session_counter: int = 0  # monotonic counter for unique session IDs

    def on_mount(self) -> None:
        for conn in self._pending_connections:
            self.add_connection_tab(conn)
        self._pending_connections.clear()

    def _next_session_id(self, conn: SSHConnection) -> str:
        """Generate a unique session ID for a new tab."""
        self._session_counter += 1
        return f"{conn.id}-{self._session_counter}"

    def enqueue_connection(self, conn: SSHConnection) -> None:
        if self.is_mounted:
            self.add_connection_tab(conn)
        else:
            self._pending_connections.append(conn)

    def compose(self) -> ComposeResult:
        with TabbedContent(id="tabs"):
            pass
        yield Static(" Idle", id="telemetry-status")

    def _get_active_session_id(self) -> str | None:
        """Get the session_id of the currently active tab."""
        tabs = self.query("TabbedContent")
        if tabs:
            tc = tabs.first()
            if tc.active:
                # tc.active is the pane id like "tab-{session_id}"
                return tc.active.replace("tab-", "")
        return None

    def get_active_connection_id(self) -> str | None:
        """Get the connection_id from the active tab's session."""
        session_id = self._get_active_session_id()
        if session_id and session_id in self._tabs_data:
            return self._tabs_data[session_id].id
        return None

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._scroll_hint = ""
        session_id = self._get_active_session_id()
        self._update_telemetry_bar(session_id)
        # Focus the terminal emulator in the active tab
        if session_id:
            try:
                term = self.query_one(f"#term-{session_id}", TerminalEmulator)
                term.focus()
            except Exception:
                pass

    def on_terminal_emulator_scroll_changed(self, event: TerminalEmulator.ScrollChanged) -> None:
        """Update status bar when user scrolls in terminal history."""
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
        if not session_id or session_id not in self._tabs_data:
            status.update(" No Active Connection")
            return

        conn = self._tabs_data[session_id]
        err = get_color(self.app.theme, "error")
        telemetry = self._telemetry_data.get(session_id, "Loading...")
        
        if self._scroll_hint:
            status.update(
                f"[bold yellow]{self._scroll_hint}[/] | "
                f"[bold {err}]Ctrl+D[/] Close  [bold {err}]Ctrl+B[/] Dash"
            )
        else:
            status.update(
                f" {telemetry} | "
                f"[bold {err}]Ctrl+D[/] Close  [bold {err}]Alt+P[/] Snippets  [bold {err}]Ctrl+F[/] Files  [bold {err}]Ctrl+B[/] Dash"
            )

    @work
    async def add_connection_tab(self, connection: SSHConnection) -> None:
        # Always create a new session - allows multiple tabs for same server
        session_id = self._next_session_id(connection)
        tab_id = f"tab-{session_id}"
        tabs = self.query_one("#tabs", TabbedContent)

        # Count how many tabs already open for this server
        count = sum(1 for c in self._tabs_data.values() if c.id == connection.id)
        tab_label = connection.name if count == 0 else f"{connection.name} ({count + 1})"

        pane = TabPane(tab_label, id=tab_id)
        await tabs.add_pane(pane)
        tabs.active = tab_id
        self._tabs_data[session_id] = connection
        
        try:
            channel = await self.app.ssh_manager.open_shell(connection.id)
            emulator = TerminalEmulator(channel, id=f"term-{session_id}")
            await pane.mount(emulator)
            emulator.focus()
            
            self._start_telemetry(session_id, connection)
        except Exception as e:
            await pane.mount(Static(f"Failed to connect: {e}"))
            
        self._update_telemetry_bar(session_id)

    def _start_telemetry(self, session_id: str, conn: SSHConnection) -> None:
        async def loop():
            client = self.app.ssh_manager.get_client(conn.id)
            if not client: return
            
            cmd = "cat /proc/stat /proc/meminfo /proc/net/dev; echo '---'; df -B1 /"
            while session_id in self._tabs_data:
                if self._get_active_session_id() == session_id:
                    try:
                        res = await client.run(cmd)
                        out = res.stdout if hasattr(res, 'stdout') else ""
                        parsed = self._parse_telemetry(session_id, str(out))
                        self._telemetry_data[session_id] = parsed
                        self._update_telemetry_bar(session_id)
                    except asyncio.CancelledError:
                        return
                    except Exception as e:
                        self._telemetry_data[session_id] = f"Telemetry Error: {type(e).__name__} {e}"
                        self._update_telemetry_bar(session_id)
                await asyncio.sleep(3)

        task = asyncio.create_task(loop())
        self._telemetry_tasks[session_id] = task

    def _parse_telemetry(self, session_id: str, raw: str) -> str:
        import time
        now = time.time()
        lines = raw.split('\n')
        cpu_usage = 0.0
        mem_pct = 0.0
        disk_str = ""
        
        mem_total = 0
        mem_avail = 0
        
        rx_bytes = 0
        tx_bytes = 0
        
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
                    
                    if session_id not in self._last_stats:
                        self._last_stats[session_id] = {}
                        
                    if "last_total" in self._last_stats[session_id]:
                        dt = total - self._last_stats[session_id]["last_total"]
                        di = idle_t - self._last_stats[session_id]["last_idle"]
                        if dt > 0:
                            cpu_usage = (dt - di) / dt * 100
                    self._last_stats[session_id]["last_total"] = total
                    self._last_stats[session_id]["last_idle"] = idle_t
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
            elif ":" in line and ("eth" in line or "ens" in line or "enp" in line or "wlan" in line):
                # /proc/net/dev interface lines
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
                    df_parts = lines[idx+2].split()
                    if len(df_parts) > 4:
                        disk_str = df_parts[4]
                        
        if mem_total > 0:
            mem_pct = (mem_total - mem_avail) / mem_total * 100
            
        # Calculate network rate
        rx_rate_str = "0KB/s"
        tx_rate_str = "0KB/s"
        
        if session_id not in self._last_stats:
            self._last_stats[session_id] = {}
            
        stats = self._last_stats[session_id]
        if "last_rx" in stats and "last_ts" in stats:
            dt = now - stats["last_ts"]
            if dt > 0:
                rx_rate = (rx_bytes - stats["last_rx"]) / dt
                tx_rate = (tx_bytes - stats["last_tx"]) / dt
                
                # Format to KB/MB
                def fmt_rate(b: float) -> str:
                    if b < 0: return "0KB/s"
                    if b > 1024 * 1024: return f"{b/1024/1024:.1f}MB/s"
                    return f"{b/1024:.1f}KB/s"
                    
                rx_rate_str = fmt_rate(rx_rate)
                tx_rate_str = fmt_rate(tx_rate)
                
        stats["last_rx"] = rx_bytes
        stats["last_tx"] = tx_bytes
        stats["last_ts"] = now
            
        return f"CPU: {cpu_usage:.1f}% | Mem: {mem_pct:.1f}% | Disk: {disk_str} | ↓ {rx_rate_str} ↑ {tx_rate_str}"

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
        """Close the currently active tab/session."""
        session_id = self._get_active_session_id()
        if session_id:
            self._close_session(session_id)

    def _close_session(self, session_id: str) -> None:
        """Close a specific session tab and clean up resources."""
        if session_id not in self._tabs_data:
            return

        conn = self._tabs_data[session_id]

        # Cancel telemetry task
        if session_id in self._telemetry_tasks:
            self._telemetry_tasks[session_id].cancel()
            del self._telemetry_tasks[session_id]
        
        # Stop the terminal emulator
        try:
            term = self.query_one(f"#term-{session_id}", TerminalEmulator)
            term.stop()
        except Exception:
            pass
            
        del self._tabs_data[session_id]
        self._telemetry_data.pop(session_id, None)
        self._last_stats.pop(session_id, None)
        
        # Only disconnect the SSH connection if no other tabs use it
        other_sessions_for_conn = [
            sid for sid, c in self._tabs_data.items() if c.id == conn.id
        ]
        if not other_sessions_for_conn:
            self.app.ssh_manager.disconnect(conn.id)
        
        tabs = self.query_one("#tabs", TabbedContent)
        try:
            tabs.remove_pane(f"tab-{session_id}")
        except Exception:
            pass
        
        if len(self._tabs_data) == 0:
            self.app.switch_screen("dashboard")
            self.app.notify("All tabs closed")
        else:
            self.app.notify("Tab closed")

    def action_file_transfer(self) -> None:
        """Handle file transfers for the active tab."""
        conn_id = self.get_active_connection_id()
        if conn_id:
            conn = self._tabs_data.get(self._get_active_session_id())
            if conn:
                from ssh_term.screens.file_transfer import FileTransferScreen
                self.app.push_screen(FileTransferScreen(conn))

    @work
    async def action_search_snippet(self) -> None:
        """Open the snippet palette and inject the result into the terminal."""
        from ssh_term.screens.snippet_palette import SnippetPaletteScreen
        content = await self.app.push_screen_wait(SnippetPaletteScreen(self.app.config_manager))
        if content:
            tc = self.query_one("#tabs", TabbedContent)
            session_id = tc.active.replace("tab-", "")
            if session_id:
                try:
                    # Strip trailing newlines so it just pastes into the buffer without executing
                    cleaned_content = content.rstrip('\r\n')
                    term = self.query_one(f"#term-{session_id}", TerminalEmulator)
                    term.write_stdin(cleaned_content)
                except Exception as e:
                    self.notify(f"Failed to inject snippet: {e}", severity="error")

    def action_back_to_dash(self) -> None:
        self.app.switch_screen("dashboard")

    # --- History scrolling (screen-level handlers) ---

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
        """Screen-level fallback: forward scroll to active terminal."""
        term = self._get_active_terminal()
        if term:
            event.stop()
            term._set_scroll_offset(term._scroll_offset + 3)

    def on_mouse_scroll_down(self, event) -> None:
        """Screen-level fallback: forward scroll to active terminal."""
        term = self._get_active_terminal()
        if term:
            event.stop()
            term._set_scroll_offset(term._scroll_offset - 3)
