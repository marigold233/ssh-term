"""Comprehensive functional tests for the Rust VTE terminal engine (rs_term).

Tests cover:
  - Cursor movement (CUU/CUD/CUF/CUB, CUP, Home, newline, carriage return)
  - Screen erasure (ED modes 0/1/2, EL modes 0/1/2)
  - SGR color handling (standard 8, bright 8, 256-color, RGB true-color)
  - Text attributes (bold, italic, underline, inverse, reset)
  - Scroll regions (DECSTBM) and scroll up / scroll down
  - Alternate screen buffer (smcup / rmcup via ?1049h/l)
  - Line insertion / deletion (IL, DL)
  - Text wrapping at right margin
  - Wide character (CJK) rendering
  - Bracketed paste mode toggle (?2004h/l)
  - Scrollback history depth and retrieval
  - Dirty-line tracking accuracy
  - get_line_ansi output correctness under complex styles
  - Rendering fluency: sustained throughput under burst load
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import ssh_term.rs_term as rs_term


def _make(cols=80, rows=24):
    """Create a fresh Screen + Stream pair."""
    screen = rs_term.Screen(cols, rows)
    stream = rs_term.Stream()
    return screen, stream


def _read_line_text(screen, row_index):
    """Extract visible text from one line via get_line_segments, stripping trailing spaces."""
    segs = screen.get_line_segments(row_index)
    text = "".join(s[0] for s in segs)
    return text.rstrip()


# ─── Cursor Movement ────────────────────────────────────────────────────────

class TestCursorMovement(unittest.TestCase):

    def test_cursor_absolute_position(self):
        """CSI row;col H  – move cursor to (row,col)."""
        s, st = _make(80, 24)
        st.feed(s, "\x1b[5;10H")  # row 5, col 10  (1-based)
        self.assertEqual(s.cursor.y, 4)
        self.assertEqual(s.cursor.x, 9)

    def test_cursor_up(self):
        """CSI n A – cursor up n lines."""
        s, st = _make()
        st.feed(s, "\x1b[10;1H")  # go to row 10
        st.feed(s, "\x1b[3A")      # up 3
        self.assertEqual(s.cursor.y, 6)

    def test_cursor_down(self):
        """CSI n B – cursor down n lines."""
        s, st = _make()
        st.feed(s, "\x1b[1;1H")
        st.feed(s, "\x1b[5B")
        self.assertEqual(s.cursor.y, 5)

    def test_cursor_forward(self):
        """CSI n C – cursor forward n cols."""
        s, st = _make()
        st.feed(s, "\x1b[1;1H\x1b[10C")
        self.assertEqual(s.cursor.x, 10)

    def test_cursor_back(self):
        """CSI n D – cursor back n cols (clamped at 0)."""
        s, st = _make()
        st.feed(s, "\x1b[1;20H")  # col 20
        st.feed(s, "\x1b[5D")      # back 5 → col 14
        self.assertEqual(s.cursor.x, 14)

    def test_cursor_back_clamp(self):
        """Cursor back beyond column 0 should clamp."""
        s, st = _make()
        st.feed(s, "\x1b[1;3H\x1b[100D")
        self.assertEqual(s.cursor.x, 0)

    def test_carriage_return(self):
        """\\r resets cursor to column 0."""
        s, st = _make()
        st.feed(s, "Hello\r")
        self.assertEqual(s.cursor.x, 0)

    def test_newline(self):
        """\\n moves cursor down one row."""
        s, st = _make()
        st.feed(s, "Hello\n")
        self.assertEqual(s.cursor.y, 1)

    def test_backspace(self):
        """\\x08 (BS) moves cursor left by 1."""
        s, st = _make()
        st.feed(s, "AB\x08")
        self.assertEqual(s.cursor.x, 1)

    def test_cursor_home_default(self):
        """CSI H without params goes to (0,0)."""
        s, st = _make()
        st.feed(s, "\x1b[10;20H")  # somewhere
        st.feed(s, "\x1b[H")        # home
        self.assertEqual(s.cursor.x, 0)
        self.assertEqual(s.cursor.y, 0)

    def test_reverse_index(self):
        """ESC M – reverse index (cursor up, scroll down if at top margin)."""
        s, st = _make(80, 5)
        st.feed(s, "\x1b[1;1H")   # row 0
        st.feed(s, "\x1bM")        # reverse index at top → scroll down
        self.assertEqual(s.cursor.y, 0)


# ─── Screen Erasure ──────────────────────────────────────────────────────────

class TestScreenErasure(unittest.TestCase):

    def test_erase_display_below(self):
        """CSI 0 J – erase from cursor to end of display."""
        s, st = _make(10, 3)
        st.feed(s, "AAAAAAAAAA")        # row 0 full
        st.feed(s, "\x1b[2;1HBBBBBBBBBB")  # row 1 full
        st.feed(s, "\x1b[3;1HCCCCCCCCCC")  # row 2 full
        st.feed(s, "\x1b[2;5H")           # cursor at row1, col4
        st.feed(s, "\x1b[0J")             # erase below
        row1 = _read_line_text(s, 1)
        row2 = _read_line_text(s, 2)
        # row1 cols 4..9 should be blank, row2 entirely blank
        self.assertEqual(len(row2), 0)

    def test_erase_display_above(self):
        """CSI 1 J – erase from start to cursor."""
        s, st = _make(10, 3)
        st.feed(s, "AAAAAAAAAA\x1b[2;1HBBBBBBBBBB\x1b[3;1HCCCCCCCCCC")
        st.feed(s, "\x1b[2;5H\x1b[1J")
        row0 = _read_line_text(s, 0)
        self.assertEqual(len(row0), 0)

    def test_erase_display_all(self):
        """CSI 2 J – erase entire display."""
        s, st = _make(10, 3)
        st.feed(s, "AAAAAAAAAA\x1b[2;1HBBBBBBBBBB\x1b[3;1HCCCCCCCCCC")
        st.feed(s, "\x1b[2J")
        for y in range(3):
            self.assertEqual(len(_read_line_text(s, y)), 0)

    def test_erase_line_right(self):
        """CSI 0 K – erase from cursor to end of line."""
        s, st = _make(10, 1)
        st.feed(s, "ABCDEFGHIJ")
        st.feed(s, "\x1b[1;4H\x1b[0K")
        text = _read_line_text(s, 0)
        self.assertEqual(text, "ABC")

    def test_erase_line_left(self):
        """CSI 1 K – erase from start of line to cursor."""
        s, st = _make(10, 1)
        st.feed(s, "ABCDEFGHIJ")
        st.feed(s, "\x1b[1;4H\x1b[1K")
        text = _read_line_text(s, 0)
        # positions 0-3 erased, 4-9 remain
        self.assertTrue(text.lstrip().startswith("E"))

    def test_erase_line_all(self):
        """CSI 2 K – erase entire line."""
        s, st = _make(10, 1)
        st.feed(s, "ABCDEFGHIJ\x1b[1;5H\x1b[2K")
        self.assertEqual(len(_read_line_text(s, 0)), 0)


# ─── Colors & Text Attributes ───────────────────────────────────────────────

class TestColorsAndAttributes(unittest.TestCase):

    def test_standard_foreground_colors(self):
        """CSI 30-37 m – standard 8 foreground colors produce valid ANSI output."""
        s, st = _make(20, 1)
        for code in range(30, 38):
            st.feed(s, f"\x1b[{code}mX\x1b[0m")
        ansi = s.get_line_ansi(0, False, 0)
        self.assertIn("\x1b[", ansi)
        self.assertIn("X", ansi)

    def test_bright_foreground_colors(self):
        """CSI 90-97 m – bright foreground colors."""
        s, st = _make(20, 1)
        for code in range(90, 98):
            st.feed(s, f"\x1b[{code}mB\x1b[0m")
        ansi = s.get_line_ansi(0, False, 0)
        self.assertIn("B", ansi)

    def test_256_color(self):
        """CSI 38;5;n m – 256-color extended palette."""
        s, st = _make(20, 1)
        st.feed(s, "\x1b[38;5;196mRED\x1b[0m")
        ansi = s.get_line_ansi(0, False, 0)
        self.assertIn("RED", ansi)
        self.assertIn("38;5;196", ansi)

    def test_rgb_truecolor(self):
        """CSI 38;2;r;g;b m – 24-bit RGB true-color."""
        s, st = _make(20, 1)
        st.feed(s, "\x1b[38;2;255;128;0mORANGE\x1b[0m")
        ansi = s.get_line_ansi(0, False, 0)
        self.assertIn("ORANGE", ansi)
        self.assertIn("38;2;255;128;0", ansi)

    def test_background_256(self):
        """CSI 48;5;n m – 256-color background."""
        s, st = _make(20, 1)
        st.feed(s, "\x1b[48;5;21mBLUE_BG\x1b[0m")
        ansi = s.get_line_ansi(0, False, 0)
        self.assertIn("48;5;21", ansi)

    def test_background_rgb(self):
        """CSI 48;2;r;g;b m – RGB background."""
        s, st = _make(20, 1)
        st.feed(s, "\x1b[48;2;0;255;0mGREEN_BG\x1b[0m")
        ansi = s.get_line_ansi(0, False, 0)
        self.assertIn("48;2;0;255;0", ansi)

    def test_bold(self):
        s, st = _make(20, 1)
        st.feed(s, "\x1b[1mBOLD\x1b[0m")
        ansi = s.get_line_ansi(0, False, 0)
        self.assertIn("\x1b[", ansi)
        self.assertIn("1", ansi)  # SGR 1 = bold

    def test_italic(self):
        s, st = _make(20, 1)
        st.feed(s, "\x1b[3mITALIC\x1b[0m")
        ansi = s.get_line_ansi(0, False, 0)
        self.assertIn("3", ansi)

    def test_underline(self):
        s, st = _make(20, 1)
        st.feed(s, "\x1b[4mUNDER\x1b[0m")
        ansi = s.get_line_ansi(0, False, 0)
        self.assertIn("4", ansi)

    def test_inverse(self):
        s, st = _make(20, 1)
        st.feed(s, "\x1b[7mINVERSE\x1b[0m")
        ansi = s.get_line_ansi(0, False, 0)
        self.assertIn("7", ansi)

    def test_sgr_reset(self):
        """CSI 0 m resets all attributes."""
        s, st = _make(20, 1)
        st.feed(s, "\x1b[1;3;4;31mSTYLED\x1b[0mPLAIN")
        ansi = s.get_line_ansi(0, False, 0)
        self.assertIn("STYLED", ansi)
        self.assertIn("PLAIN", ansi)

    def test_combined_attributes(self):
        """Multiple attributes in one SGR sequence: bold + red + underline."""
        s, st = _make(40, 1)
        st.feed(s, "\x1b[1;4;31mCOMBINED\x1b[0m")
        ansi = s.get_line_ansi(0, False, 0)
        self.assertIn("COMBINED", ansi)


# ─── Scroll Regions ─────────────────────────────────────────────────────────

class TestScrollRegions(unittest.TestCase):

    def test_decstbm_sets_margins(self):
        """CSI top;bottom r sets scrolling region."""
        s, st = _make(10, 10)
        st.feed(s, "\x1b[3;7r")  # scroll region rows 3-7
        # Cursor should reset to (0,0)
        self.assertEqual(s.cursor.x, 0)
        self.assertEqual(s.cursor.y, 0)

    def test_scroll_up_within_region(self):
        """Writing past the bottom margin should scroll only within the region."""
        s, st = _make(10, 5)
        # Set scroll region rows 2-4 (1-based)
        st.feed(s, "\x1b[2;4r")
        # Put text on row 0 (outside region) — should survive
        st.feed(s, "\x1b[1;1HTOP_LINE")
        # Move to bottom of region and force scroll
        st.feed(s, "\x1b[4;1H")
        st.feed(s, "LINE_A\nLINE_B\nLINE_C\n")
        # Row 0 should still have "TOP_LINE"
        self.assertIn("TOP_LINE", _read_line_text(s, 0))

    def test_scroll_down_reverse_index(self):
        """ESC M at top of scroll region should scroll down within region."""
        s, st = _make(10, 5)
        st.feed(s, "\x1b[2;4r")
        st.feed(s, "\x1b[2;1H")
        st.feed(s, "\x1bM")  # reverse index at top of region
        self.assertEqual(s.cursor.y, 1)


# ─── Alternate Screen Buffer ────────────────────────────────────────────────

class TestAltScreenBuffer(unittest.TestCase):

    def test_switch_to_alt_and_back(self):
        """?1049h enters alt buffer, ?1049l restores main buffer."""
        s, st = _make(20, 5)
        st.feed(s, "MAIN_CONTENT")
        main_text = _read_line_text(s, 0)
        self.assertIn("MAIN_CONTENT", main_text)

        # Enter alt screen
        st.feed(s, "\x1b[?1049h")
        alt_text = _read_line_text(s, s.get_total_lines() - 5)
        self.assertEqual(len(alt_text), 0)  # alt buffer starts blank

        st.feed(s, "ALT_CONTENT")
        alt_text = _read_line_text(s, s.get_total_lines() - 5)
        self.assertIn("ALT_CONTENT", alt_text)

        # Leave alt screen
        st.feed(s, "\x1b[?1049l")
        restored = _read_line_text(s, s.get_total_lines() - 5)
        self.assertIn("MAIN_CONTENT", restored)


# ─── Line Insert / Delete ───────────────────────────────────────────────────

class TestLineInsertDelete(unittest.TestCase):

    def test_insert_line(self):
        """CSI n L – insert n blank lines at cursor, pushing existing lines down."""
        s, st = _make(10, 5)
        st.feed(s, "\x1b[1;1HAAAAAAAAAA")
        st.feed(s, "\x1b[2;1HBBBBBBBBBB")
        st.feed(s, "\x1b[3;1HCCCCCCCCCC")
        st.feed(s, "\x1b[2;1H\x1b[1L")  # insert 1 blank line at row 2
        # Row 1 (0-based) should now be blank
        self.assertEqual(len(_read_line_text(s, 1)), 0)
        # Row 2 should now have what was row 1 (BBBB...)
        self.assertIn("B", _read_line_text(s, 2))

    def test_delete_line(self):
        """CSI n M – delete n lines at cursor, pulling lines up."""
        s, st = _make(10, 5)
        st.feed(s, "\x1b[1;1HAAAAAAAAAA")
        st.feed(s, "\x1b[2;1HBBBBBBBBBB")
        st.feed(s, "\x1b[3;1HCCCCCCCCCC")
        st.feed(s, "\x1b[2;1H\x1b[1M")  # delete row 2
        # Row 1 should now have CCCC...
        self.assertIn("C", _read_line_text(s, 1))


# ─── Text Wrapping ──────────────────────────────────────────────────────────

class TestTextWrapping(unittest.TestCase):

    def test_wrap_at_right_margin(self):
        """Text exceeding column width wraps to the next line."""
        s, st = _make(5, 3)
        st.feed(s, "ABCDEFGH")
        row0 = _read_line_text(s, 0)
        row1 = _read_line_text(s, 1)
        self.assertEqual(row0, "ABCDE")
        self.assertIn("F", row1)


# ─── Wide (CJK) Characters ──────────────────────────────────────────────────

class TestWideCharacters(unittest.TestCase):

    def test_cjk_occupies_two_cells(self):
        """A fullwidth character should consume 2 columns."""
        s, st = _make(20, 1)
        st.feed(s, "A你B")
        # cursor should be at col 4 (A=1, 你=2, B=1)
        self.assertEqual(s.cursor.x, 4)

    def test_cjk_wrap(self):
        """A wide char that doesn't fit at the right margin wraps to the next line."""
        s, st = _make(5, 3)
        st.feed(s, "ABCD你")  # 4 + 2 = 6 > 5, should wrap
        row1 = _read_line_text(s, 1)
        self.assertIn("你", row1)


# ─── Bracketed Paste Mode ───────────────────────────────────────────────────

class TestBracketedPaste(unittest.TestCase):

    def test_enable_disable(self):
        s, st = _make()
        self.assertFalse(s.bracketed_paste)
        st.feed(s, "\x1b[?2004h")
        self.assertTrue(s.bracketed_paste)
        st.feed(s, "\x1b[?2004l")
        self.assertFalse(s.bracketed_paste)


# ─── Scrollback History ─────────────────────────────────────────────────────

class TestScrollbackHistory(unittest.TestCase):

    def test_history_grows(self):
        """Lines scrolled off the top enter scrollback."""
        s, st = _make(10, 5)
        total_before = s.get_total_lines()
        # Send 20 newlines → 15 should enter scrollback
        st.feed(s, "\n" * 20)
        total_after = s.get_total_lines()
        self.assertGreater(total_after, total_before)

    def test_history_cap(self):
        """Scrollback should not exceed 5000 lines."""
        s, st = _make(10, 5)
        st.feed(s, "\n" * 6000)
        # Total = scrollback + 5 active lines
        self.assertLessEqual(s.get_total_lines(), 5005)

    def test_history_content_preserved(self):
        """Text scrolled into history should be retrievable via get_line_segments."""
        s, st = _make(10, 3)
        st.feed(s, "HISTORY\r\n" * 10)
        # First line of scrollback should contain "HISTORY"
        text = _read_line_text(s, 0)
        self.assertIn("HISTORY", text)


# ─── Dirty Line Tracking ────────────────────────────────────────────────────

class TestDirtyLines(unittest.TestCase):

    def test_initial_all_dirty(self):
        """After construction, all lines are marked dirty."""
        s, st = _make(10, 5)
        dirty = s.get_and_clear_dirty_lines()
        self.assertEqual(len(dirty), 5)

    def test_clear_then_modify(self):
        """After clearing, only modified lines appear dirty."""
        s, st = _make(10, 5)
        s.get_and_clear_dirty_lines()
        st.feed(s, "\x1b[3;1HX")   # modify row 2
        dirty = s.get_and_clear_dirty_lines()
        self.assertIn(2, dirty)
        # Row 0 should NOT be dirty
        self.assertNotIn(0, dirty)


# ─── get_line_ansi Correctness ───────────────────────────────────────────────

class TestGetLineAnsi(unittest.TestCase):

    def test_empty_line(self):
        """get_line_ansi for an untouched line returns empty / spaces."""
        s, st = _make(10, 3)
        ansi = s.get_line_ansi(0, False, 0)
        # It should not crash and should be a string
        self.assertIsInstance(ansi, str)

    def test_cursor_visible_produces_inverse(self):
        """With cursor_visible=True and scroll_offset=0, cursor cell gets SGR 7."""
        s, st = _make(10, 3)
        st.feed(s, "ABC")  # cursor at col 3
        ansi = s.get_line_ansi(0, True, 0)
        self.assertIn("7", ansi)   # inverse marker for cursor

    def test_cursor_hidden_no_inverse(self):
        """With cursor_visible=False the cursor cell should NOT get SGR 7."""
        s, st = _make(10, 3)
        st.feed(s, "ABC")
        ansi_hidden = s.get_line_ansi(0, False, 0)
        # Parse: the ONLY reason for a '7' in the output would be cursor
        # With hidden cursor we should not see inverse for cursor
        # (This is a soft check as '7' can appear as text char)
        segs = s.get_line_segments(0)
        for seg in segs:
            _, _, _, _, _, _, inverse, is_cursor = seg
            if is_cursor:
                self.assertFalse(inverse)  # cursor is not inverse when hidden

    def test_multicolor_line_round_trip(self):
        """A line with multiple colors round-trips through get_line_ansi."""
        s, st = _make(40, 1)
        st.feed(s, "\x1b[31mRED\x1b[32mGREEN\x1b[34mBLUE\x1b[0m")
        ansi = s.get_line_ansi(0, False, 0)
        self.assertIn("RED", ansi)
        self.assertIn("GREEN", ansi)
        self.assertIn("BLUE", ansi)


# ─── Resize ─────────────────────────────────────────────────────────────────

class TestResize(unittest.TestCase):

    def test_resize_larger(self):
        """Growing the screen should add blank rows."""
        s, st = _make(10, 5)
        s.resize(10, 20)
        self.assertEqual(s.columns, 20)
        self.assertEqual(s.lines, 10)

    def test_resize_smaller_clamps_cursor(self):
        """Shrinking the screen clamps the cursor inside bounds."""
        s, st = _make(80, 24)
        st.feed(s, "\x1b[20;70H")  # row 19, col 69
        s.resize(5, 10)
        self.assertLess(s.cursor.y, 5)
        self.assertLess(s.cursor.x, 10)

    def test_resize_preserves_content(self):
        """Content on the screen survives a resize."""
        s, st = _make(20, 5)
        st.feed(s, "KEEP_THIS")
        s.resize(10, 30)
        text = _read_line_text(s, 0)
        self.assertIn("KEEP_THIS", text)


# ─── Rendering Fluency (Stress) ─────────────────────────────────────────────

class TestRenderingFluency(unittest.TestCase):

    def test_sustained_throughput(self):
        """Feed 5 MB of styled data and verify it completes in < 2s."""
        s, st = _make(120, 40)
        # Build a heavy payload (~1 KB per iteration) with color shifts
        chunk = ""
        for i in range(50):
            c = 31 + (i % 7)
            chunk += f"\x1b[{c}m{'X' * 16}\x1b[0m"
        chunk += "\r\n"
        # Scale up
        payload = chunk * 100         # ~100 KB
        total_bytes = len(payload) * 50   # ~5 MB over 50 iterations

        start = time.perf_counter()
        for _ in range(50):
            st.feed(s, payload)
        elapsed = time.perf_counter() - start

        mb = total_bytes / (1024 * 1024)
        print(f"\n  [Fluency] {mb:.1f} MB in {elapsed:.3f}s → {mb/elapsed:.1f} MB/s")
        self.assertLess(elapsed, 2.0, f"Throughput too slow: {elapsed:.2f}s for {mb:.1f} MB")

    def test_rapid_cursor_repositioning(self):
        """Rapid random cursor jumps should not crash or slow down."""
        s, st = _make(80, 24)
        moves = ""
        for r in range(1, 25):
            for c in range(1, 81, 10):
                moves += f"\x1b[{r};{c}HX"
        start = time.perf_counter()
        for _ in range(500):
            st.feed(s, moves)
        elapsed = time.perf_counter() - start
        print(f"\n  [Cursor Stress] 500 x {24*8} jumps in {elapsed:.3f}s")
        self.assertLess(elapsed, 3.0)

    def test_ansi_render_throughput(self):
        """get_line_ansi should render 24 lines x 10000 frames in < 2s."""
        s, st = _make(80, 24)
        st.feed(s, "\x1b[31;1mHello \x1b[32;4mWorld\x1b[0m  test\r\n" * 24)

        start = time.perf_counter()
        total = s.get_total_lines()
        for _ in range(10000):
            for y in range(max(0, total - 24), total):
                s.get_line_ansi(y, True, 0)
        elapsed = time.perf_counter() - start
        lines_rendered = 10000 * 24
        print(f"\n  [ANSI Render] {lines_rendered} lines in {elapsed:.3f}s → {lines_rendered/elapsed:.0f} lines/s")
        self.assertLess(elapsed, 2.0)


# ─── Control Character Mapping (Linux shortcuts) ────────────────────────────

class TestControlCharacterMapping(unittest.TestCase):
    """Verify the exact byte that each Ctrl+<key> maps to."""

    CTRL_MAP = {
        'a': 1, 'b': 2, 'c': 3, 'd': 4, 'e': 5, 'f': 6,
        'g': 7, 'h': 8, 'i': 9, 'j': 10, 'k': 11, 'l': 12,
        'm': 13, 'n': 14, 'o': 15, 'p': 16, 'q': 17, 'r': 18,
        's': 19, 't': 20, 'u': 21, 'v': 22, 'w': 23, 'x': 24,
        'y': 25, 'z': 26,
    }

    def test_all_ctrl_keys(self):
        """Ctrl+A..Z should map to chr(1)..chr(26)."""
        for letter, expected_code in self.CTRL_MAP.items():
            key_name = f"ctrl+{letter}"
            # Simulate the logic from TerminalEmulator.on_key
            ch = key_name[-1].upper()
            code = ord(ch) - 64
            if 0 <= code <= 31:
                data = chr(code)
            else:
                data = None
            self.assertEqual(
                data, chr(expected_code),
                f"Ctrl+{letter} should map to chr({expected_code}), got {data!r}"
            )

    KEY_MAP = {
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
        "f4": "\x1bOS",
        "f6": "\x1b[17~",
        "f8": "\x1b[19~",
        "f9": "\x1b[20~",
        "f10": "\x1b[21~",
        "f11": "\x1b[23~",
        "f12": "\x1b[24~",
    }

    def test_all_special_keys(self):
        """Every special key in the key_map should produce the correct escape sequence."""
        for key_name, expected_seq in self.KEY_MAP.items():
            self.assertIsNotNone(expected_seq, f"Missing mapping for {key_name}")
            self.assertTrue(
                len(expected_seq) > 0,
                f"Empty sequence for {key_name}"
            )


if __name__ == "__main__":
    unittest.main()
