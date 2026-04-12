"""Terminal emulator widget using rs_term + asyncssh."""

from __future__ import annotations

import asyncio
import asyncssh
import ssh_term.rs_term as rs_term
from rich.text import Text
from textual import work
from textual.widget import Widget
from textual.strip import Strip
from textual.events import Key, Resize

from ssh_term.theme import TERMINAL_FG, TERMINAL_BG, TERMINAL_ANSI


_PYTE_COLOR_MAP = {
    "black": TERMINAL_ANSI[0],
    "red": TERMINAL_ANSI[1],
    "green": TERMINAL_ANSI[2],
    "brown": TERMINAL_ANSI[3],
    "blue": TERMINAL_ANSI[4],
    "magenta": TERMINAL_ANSI[5],
    "cyan": TERMINAL_ANSI[6],
    "white": TERMINAL_ANSI[7],
    "brightblack": TERMINAL_ANSI[8],
    "brightred": TERMINAL_ANSI[9],
    "brightgreen": TERMINAL_ANSI[10],
    "brightyellow": TERMINAL_ANSI[11],
    "brightblue": TERMINAL_ANSI[12],
    "brightmagenta": TERMINAL_ANSI[13],
    "brightcyan": TERMINAL_ANSI[14],
    "brightwhite": TERMINAL_ANSI[15],
}


def _get_256_color(n: int) -> str:
    if n < 16:
        return TERMINAL_ANSI[n]
    if n < 232:
        n -= 16
        r = (n // 36) * 51
        g = ((n // 6) % 6) * 51
        b = (n % 6) * 51
        return f"#{r:02x}{g:02x}{b:02x}"
    if n < 256:
        v = (n - 232) * 10 + 8
        return f"#{v:02x}{v:02x}{v:02x}"
    return ""

def _resolve_color(color: str, default: str) -> str:
    if not color or color == "default":
        return default
    if color in _PYTE_COLOR_MAP:
        return _PYTE_COLOR_MAP[color]
    if color.startswith("color"):
        try:
            return _get_256_color(int(color[5:]))
        except ValueError:
            pass
    if len(color) == 6 or len(color) == 7:
        if color.startswith("#"):
            return color
        try:
            int(color, 16)
            return f"#{color}"
        except ValueError:
            pass
    return default


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

    class KeyBroadcast(Message):
        """Emitted after a key is sent to the local pty so broadcast mode can replicate it."""
        def __init__(self, data: str) -> None:
            super().__init__()
            self.data = data

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

    def on_mount(self) -> None:
        self.set_interval(0.5, self._toggle_cursor)
        self._read_channel()

    def _toggle_cursor(self) -> None:
        self._cursor_visible = not self._cursor_visible
        # Only bother refreshing if we're on the live view where cursor matters
        if self._scroll_offset == 0:
            self.refresh()

    @work
    async def _read_channel(self) -> None:
        try:
            while not self._stop_process:
                data = await self.process.stdout.read(4096)
                if not data:
                    break
                self.stream.feed(self._pyte_screen, data)
                # When new data arrives and user is at/near bottom, stay at bottom
                if self._scroll_offset <= 2:
                    self._scroll_offset = 0
                self.refresh()
        except Exception:
            pass
        self._on_disconnect()

    def _on_disconnect(self) -> None:
        self.post_message(self.Disconnected(self.id))

    @property
    def _max_scroll_offset(self) -> int:
        """Maximum lines user can scroll up (= total history lines)."""
        total = self._pyte_screen.get_total_lines()
        return max(0, total - self._rows)

    def _set_scroll_offset(self, value: int) -> None:
        """Set scroll offset clamped to valid range, and notify parent."""
        old = self._scroll_offset
        self._scroll_offset = max(0, min(self._max_scroll_offset, value))
        if self._scroll_offset != old:
            self.post_message(self.ScrollChanged(self._scroll_offset, self._max_scroll_offset))
            self.refresh()

    def render_line(self, y: int) -> Strip:
        """Render a single visible line.
        
        y is 0..(_rows-1) relative to the widget viewport.
        We map it to an absolute line index into Rust's unified buffer.
        """
        total = self._pyte_screen.get_total_lines()
        # absolute_y: which line in the full (history + live) buffer to display
        # When _scroll_offset=0, we show the last _rows lines (the live terminal)
        # When _scroll_offset=N, we shift the window up by N lines
        start_line = max(0, total - self._rows - self._scroll_offset)
        absolute_y = start_line + y

        if absolute_y >= total or absolute_y < 0:
            try:
                style = self.app.console.get_style(f"on {TERMINAL_BG}")
                return Strip.blank(self._cols, style)
            except Exception:
                return Strip.blank(self._cols)

        text = Text()
        for chunk, fg, bg, bold, italics, underscore, inverse, is_cursor in self._pyte_screen.get_line_segments(absolute_y):
            fg_res = _resolve_color(fg, TERMINAL_FG)
            bg_res = _resolve_color(bg, TERMINAL_BG)
            # Only show cursor when viewing the live (bottom) view
            if (is_cursor and self._cursor_visible and self._scroll_offset == 0) or inverse:
                fg_res, bg_res = bg_res, fg_res
            style_str = f"{fg_res} on {bg_res}"
            if bold:
                style_str += " bold"
            if italics:
                style_str += " italic"
            if underscore:
                style_str += " underline"
            text.append(chunk, style=style_str)

        try:
            segments = list(text.render(self.app.console))
        except Exception:
            return Strip.blank(self._cols)
        return Strip(segments, self._cols)

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
            ch = key[-1]
            code = ord(ch.lower()) - ord("a") + 1
            data = chr(code)
        elif event.character:
            data = event.character

        if data:
            try:
                self.process.stdin.write(data)
                # Snap back to live view on any keypress
                if self._scroll_offset != 0:
                    self._scroll_offset = 0
                    self.post_message(self.ScrollChanged(0, self._max_scroll_offset))
                self.refresh()
                # Notify parent for broadcast fan-out
                self.post_message(self.KeyBroadcast(data))
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
                self.post_message(self.ScrollChanged(0, self._max_scroll_offset))
            self.refresh()
        except Exception as e:
            pass

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
            self._pyte_screen.resize(rows, cols)
            try:
                self.process.change_terminal_size(cols, rows)
            except Exception:
                pass
            # Reset scroll on resize to avoid stale offsets
            self._scroll_offset = 0

    def stop(self) -> None:
        self._stop_process = True
        try:
            self.process.close()
        except Exception:
            pass
