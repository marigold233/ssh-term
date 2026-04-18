import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

try:
    from ssh_term.widgets.terminal_emulator import TerminalEmulator
    from textual.events import Key
    import queue
except ImportError:
    pass # In case dependencies are missing in CI

class TestTerminalEmulator(unittest.IsolatedAsyncioTestCase):
    async def test_initialization(self):
        """Test terminal creates its underlying thread queue safely"""
        mock_process = MagicMock()
        mock_process.stdout.read = AsyncMock()
        term = TerminalEmulator(mock_process)
        
        self.assertIsInstance(term._data_queue, queue.Queue)
        self.assertFalse(term._terminal_updated)
        self.assertEqual(term._scroll_offset, 0)
        
    async def test_data_feeding_functionality(self):
        """Test simulating network receiving and consumer formatting"""
        mock_process = MagicMock()
        term = TerminalEmulator(mock_process)
        
        # 1. Simulate _read_channel inserting network data
        test_data = "Hello \x1b[31mTerminal\x1b[0m\r\n"
        term._data_queue.put(test_data)
        
        # 2. Simulate the _vte_consumer pulling and parsing
        try:
            data = term._data_queue.get_nowait()
            term.stream.feed(term._pyte_screen, data)
            term._terminal_updated = True
        except queue.Empty:
            self.fail("Queue should have captured the data from async bounds.")
            
        self.assertTrue(term._terminal_updated)
        
        # 3. Test Rust's newly added C-Extension formatting
        ansi = term._pyte_screen.get_line_ansi(0, True, 0)
        self.assertIn("Hello", ansi)
        self.assertIn("Terminal", ansi)
        # Should contain ANSI code wrapper
        self.assertIn("\x1b[", ansi)

    def test_scroll_offset_bounds(self):
        """Test that scrolling handles its boundaries safely"""
        mock_process = MagicMock()
        term = TerminalEmulator(mock_process)
        
        # Initially total lines is 24, viewport is 24, max offset = 0
        term._set_scroll_offset(5)
        # Should clamp to 0 max
        self.assertEqual(term._scroll_offset, 0)
        
        # Feed 100 newlines to push history back
        term.stream.feed(term._pyte_screen, "\n" * 100)
        
        # Try scrolling now
        term._set_scroll_offset(50)
        self.assertEqual(term._scroll_offset, 50)

if __name__ == "__main__":
    unittest.main()
