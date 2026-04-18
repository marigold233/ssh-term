"""Terminal emulator widget using rs_term + asyncssh."""

from __future__ import annotations

import asyncio
import asyncssh
import ssh_term.rs_term as rs_term
import queue
import time
import threading
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import work
from textual.widget import Widget
from textual.strip import Strip
from textual.geometry import Region
from textual.events import Key, Resize, Paste

from ssh_term.theme import TERMINAL_FG, TERMINAL_BG, TERMINAL_ANSI

class TerminalEmulator(Widget, can_focus=True):
    DEFAULT_CSS = """
    TerminalEmulator {
        width: 1fr;
        height: 1fr;
        overflow: hidden;
        background: """ + TERMINAL_BG + """;
    }
    """

    from textual.message import Message

    class Disconnected(Message):
        def __init__(self, term_id: str) -> None:
            super().__init__()
            self.term_id = term_id

    class ScrollChanged(Message):
        """Emitted when scroll offset changes so parent can update status bar."""
        def __init__(self, offset: int, max_offset: int) -> None:
            super().__init__()
            self.offset = offset
            self.max_offset = max_offset


    def __init__(self, process: asyncssh.SSHClientProcess, **kwargs) -> None:
        super().__init__(**kwargs)
        self.process = process
        self._cols = 80
        self._rows = 24
        self._pyte_screen = rs_term.Screen(self._cols, self._rows)
        self.stream = rs_term.Stream()
        self._stop_process = False
        self._cursor_visible = True
        self._scroll_offset = 0  # 0 = at bottom (live view), >0 = scrolled up N lines
        self._full_redraw = False
        self._terminal_updated = False
        self._data_queue = queue.Queue()
        self._screen_lock = threading.Lock()
        self._blank_strip = None
        self._blank_strip_cols = -1

    def on_mount(self) -> None:
        self.set_interval(0.5, self._toggle_cursor)
        self.set_interval(1 / 60, self._render_tick)
        self._read_channel()
        self._vte_consumer()

    @property
    def blank_strip(self) -> Strip:
        if not self._blank_strip or self._blank_strip_cols != self._cols:
            self._blank_strip = Strip.blank(self._cols, Style(bgcolor=TERMINAL_BG))
            self._blank_strip_cols = self._cols
        return self._blank_strip

    def _render_tick(self) -> None:
        if self._full_redraw:
            self._full_redraw = False
            self._terminal_updated = False
            with self._screen_lock:
                try: self._pyte_screen.get_and_clear_dirty_lines()
                except Exception: pass
            self.refresh()
        elif self._terminal_updated:
            self._terminal_updated = False
            with self._screen_lock:
                try:
                    lines = self._pyte_screen.get_and_clear_dirty_lines()
                except Exception:
                    lines = None
            
            if self._scroll_offset != 0 or lines is None:
                self.refresh()
            else:
                for y in lines:
                    if y < self._rows:
                        self.refresh(Region(0, y, self._cols, 1))

    def _toggle_cursor(self) -> None:
        self._cursor_visible = not self._cursor_visible
        # Only refresh the cursor's row instead of the entire screen
        if self._scroll_offset == 0:
            with self._screen_lock:
                cy = self._pyte_screen.cursor.y
            if cy < self._rows:
                self.refresh(Region(0, cy, self._cols, 1))

    @work
    async def _read_channel(self) -> None:
        """Producer: Read from SSH connection asynchronously."""
        try:
            while not self._stop_process:
                # Read chunks up to 64KB for batched processing
                data = await self.process.stdout.read(65536)
                if not data:
                    break
                self._data_queue.put(data)
        except Exception:
            pass
        self._on_disconnect()

    @work(thread=True)
    def _vte_consumer(self) -> None:
        """Consumer: Parse chunks with Rust VTE engine in a background thread."""
        while not self._stop_process:
            try:
                # Wait for data with timeout so we reliably stop when necessary
                data = self._data_queue.get(timeout=0.1)
                
                # Take all accumulated data at once (batching)
                chunks = [data]
                while True:
                    try:
                        more_data = self._data_queue.get_nowait()
                        chunks.append(more_data)
                    except queue.Empty:
                        break
                        
                merged_data = "".join(chunks)
                
                # Feed in smaller fragments to yield GIL
                chunk_size = 4096
                for i in range(0, len(merged_data), chunk_size):
                    with self._screen_lock:
                        self.stream.feed(self._pyte_screen, merged_data[i:i+chunk_size])
                    time.sleep(0)

                if self._scroll_offset <= 2:
                    self._scroll_offset = 0
                    
                self._terminal_updated = True

            except queue.Empty:
                continue

    def _on_disconnect(self) -> None:
        self.post_message(self.Disconnected(self.id))

    @property
    def _max_scroll_offset(self) -> int:
        """Maximum lines user can scroll up (= total history lines)."""
        with self._screen_lock:
            total = self._pyte_screen.get_total_lines()
        return max(0, total - self._rows)

    def _set_scroll_offset(self, value: int) -> None:
        """Set scroll offset clamped to valid range, and notify parent."""
        old = self._scroll_offset
        self._scroll_offset = max(0, min(self._max_scroll_offset, value))
        if self._scroll_offset != old:
            self._full_redraw = True
            self.post_message(self.ScrollChanged(self._scroll_offset, self._max_scroll_offset))

    def render_line(self, y: int) -> Strip:
        """Render a single visible line.
        
        y is 0..(_rows-1) relative to the widget viewport.
        We map it to an absolute line index into Rust's unified buffer.
        """
        with self._screen_lock:
            total = self._pyte_screen.get_total_lines()
        # absolute_y: which line in the full (history + live) buffer to display
        # When _scroll_offset=0, we show the last _rows lines (the live terminal)
        # When _scroll_offset=N, we shift the window up by N lines
        start_line = max(0, total - self._rows - self._scroll_offset)
        absolute_y = start_line + y

        if absolute_y >= total or absolute_y < 0:
            return self.blank_strip

        with self._screen_lock:
            ansi_str = self._pyte_screen.get_line_ansi(absolute_y, self._cursor_visible, self._scroll_offset)
        if not ansi_str:
            return self.blank_strip

        parsed_text = Text.from_ansi(ansi_str)
        segs = list(self.app.console.render(parsed_text))
        return Strip(segs, self._cols)

    def on_key(self, event: Key) -> None:
        key = event.key

        # History navigation keys - intercept before sending to remote
        if key in ("shift+pageup", "shift+page_up", "ctrl+up"):
            event.stop()
            event.prevent_default()
            self._set_scroll_offset(self._scroll_offset + self._rows)
            return
        elif key in ("shift+pagedown", "shift+page_down", "ctrl+down"):
            event.stop()
            event.prevent_default()
            self._set_scroll_offset(self._scroll_offset - self._rows)
            return
        elif key in ("shift+home", "ctrl+home"):
            event.stop()
            event.prevent_default()
            self._set_scroll_offset(self._max_scroll_offset)
            return
        elif key in ("shift+end", "ctrl+end"):
            event.stop()
            event.prevent_default()
            self._set_scroll_offset(0)
            return

        event.stop()
        event.prevent_default()

        # ── Function-key workspace commands — intercepted here so they are NOT
        #    forwarded to the remote pty. We call the screen's actions directly
        #    which is more reliable than message bubbling.
        if key == "f3":
            try:
                self.screen.action_toggle_history_search()  # type: ignore[attr-defined]
            except Exception:
                pass
            return
        elif key == "f5":
            try:
                self.screen.action_split_horizontal()  # type: ignore[attr-defined]
            except Exception:
                pass
            return
        elif key == "f7":
            try:
                self.screen.action_close_split()  # type: ignore[attr-defined]
            except Exception:
                pass
            return

        key_map = {
            "escape": "\x1b",
            "enter": "\r",
            "tab": "\t",
            "backspace": "\x7f",
            "delete": "\x1b[3~",
            "up": "\x1b[A",
            "down": "\x1b[B",
            "right": "\x1b[C",
            "left": "\x1b[D",
            "home": "\x1b[H",
            "end": "\x1b[F",
            "pageup": "\x1b[5~",
            "pagedown": "\x1b[6~",
            "insert": "\x1b[2~",
            "f1": "\x1bOP",
            "f2": "\x1bOQ",
            "f3": "\x1bOR",
            "f4": "\x1bOS",
            "f5": "\x1b[15~",
            "f6": "\x1b[17~",
            "f7": "\x1b[18~",
            "f8": "\x1b[19~",
            "f9": "\x1b[20~",
            "f10": "\x1b[21~",
            "f11": "\x1b[23~",
            "f12": "\x1b[24~",
        }

        data: str | None = None
        if key in key_map:
            data = key_map[key]
        elif key.startswith("ctrl+") and len(key) == 6:
            ch = key[-1].upper()
            code = ord(ch) - 64
            if 0 <= code <= 31:
                data = chr(code)
        elif event.character:
            data = event.character

        if data:
            try:
                self.process.stdin.write(data)
                # Snap back to live view on any keypress
                if self._scroll_offset != 0:
                    self._scroll_offset = 0
                    self._full_redraw = True
                    self.post_message(self.ScrollChanged(0, self._max_scroll_offset))
                self._terminal_updated = True
            except Exception:
                pass

    def write_stdin(self, data: str) -> None:
        """Inject arbitrary string data into the terminal."""
        if not data: return
        try:
            self.process.stdin.write(data)
            # Snap back to live view
            if self._scroll_offset != 0:
                self._scroll_offset = 0
                self._full_redraw = True
                self.post_message(self.ScrollChanged(0, self._max_scroll_offset))
            self._terminal_updated = True
        except Exception as e:
            pass

    def on_paste(self, event: Paste) -> None:
        """Handle paste events by sending the text into the terminal."""
        if event.text:
            with self._screen_lock:
                bp = getattr(self._pyte_screen, "bracketed_paste", False)
            if bp:
                self.write_stdin(f"\x1b[200~{event.text}\x1b[201~")
            else:
                self.write_stdin(event.text)

    def on_mouse_scroll_up(self, event) -> None:
        """Scroll up into history."""
        event.stop()
        event.prevent_default()
        self._set_scroll_offset(self._scroll_offset + 3)

    def on_mouse_scroll_down(self, event) -> None:
        """Scroll down towards live view."""
        event.stop()
        event.prevent_default()
        self._set_scroll_offset(self._scroll_offset - 3)

    def on_resize(self, event: Resize) -> None:
        cols = max(event.size.width, 1)
        rows = max(event.size.height, 1)
        if cols != self._cols or rows != self._rows:
            self._cols = cols
            self._rows = rows
            with self._screen_lock:
                self._pyte_screen.resize(rows, cols)
            try:
                self.process.change_terminal_size(cols, rows)
            except Exception:
                pass
            # Reset scroll on resize to avoid stale offsets
            self._scroll_offset = 0
            self._full_redraw = True

    def stop(self) -> None:
        self._stop_process = True
        try:
            self.process.close()
        except Exception:
            pass
