import unittest
import asyncio
import os
import sys
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

try:
    from textual.app import App
    from ssh_term.models.connection import SSHConnection
    from ssh_term.screens.terminal_screen import TerminalScreen
    from ssh_term.widgets.terminal_emulator import TerminalEmulator
except ImportError:
    pass

class MockSSHManager:
    def __init__(self):
        self.open_shell = AsyncMock()
        self.disconnect = MagicMock()
        self.disconnect_all = MagicMock()

class DummyTerminalApp(App):
    """A lightweight mock app to host the terminal screen without actual auth/config needs."""
    def __init__(self):
        super().__init__()
        self.ssh_manager = MockSSHManager()
        self.mock_channel = MagicMock()
        
        # Read blocks slightly to prevent infinite polling in the async queue loop
        async def mock_read(*args, **kwargs):
            await asyncio.sleep(0.5)
            # Send an empty string eventually to cleanly exit the background while loop
            return ""
            
        self.mock_channel.stdout.read = AsyncMock(side_effect=mock_read)
        self.ssh_manager.open_shell.return_value = self.mock_channel
        self.theme = "textual-dark"

    def on_mount(self):
        connection = SSHConnection(id="srv_01", name="TestBox", host="192.168.1.1", username="root", auth_method="password")
        self.push_screen(TerminalScreen(connection=connection))


class TestTerminalE2E(unittest.IsolatedAsyncioTestCase):
    
    async def test_full_textual_e2e_interactions(self):
        """Test complete UI interactions, keybindings, and component behavior."""
        app = DummyTerminalApp()
        
        async with app.run_test() as pilot:
            # 1. Wait for everything to mount
            await pilot.pause()
            
            term = None
            for _ in range(20):
                try:
                    term = app.query_one(TerminalEmulator)
                    break
                except Exception:
                    await pilot.pause(0.1)
                    
            self.assertIsNotNone(term, "TerminalEmulator failed to mount natively.")
            
            try:
                # 2. Simulate User Typing
                await pilot.press("e", "c", "h", "o", "enter")
                await pilot.pause()
                
                # Validate the stdin stream was correctly forwarded to the remote server
                app.mock_channel.stdin.write.assert_any_call("c")
                app.mock_channel.stdin.write.assert_any_call("o")
                app.mock_channel.stdin.write.assert_any_call("\r") # enter key map
                
                # 3. Simulate History Bounds
                term._pyte_screen.get_total_lines = MagicMock(return_value=200) # Mock 200 lines history
                
                # Scroll up artificially
                await pilot.press("shift+pageup")
                await pilot.pause()
                
                # Terminal scroll should rise into history lines
                self.assertEqual(term._scroll_offset, 24)
                
                # Pressing ANY normal key should snap the viewport immediately back to live
                await pilot.press("a")
                await pilot.pause()
                self.assertEqual(term._scroll_offset, 0)
                
                # 4. Global screen bindings execution
                # Hitting ctrl+d triggers the disconnect binding at the screen level
                await pilot.press("ctrl+d")
                await pilot.pause()
                
                # It should have gracefully instructed the manager to severe the connection ID
                app.ssh_manager.disconnect.assert_called_with("srv_01")
            finally:
                if hasattr(term, 'stop'):
                    term.stop()


if __name__ == "__main__":
    unittest.main()
