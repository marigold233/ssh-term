"""Screen for Batch Command and Plugin Execution."""

from __future__ import annotations

import asyncio
import re
import importlib.util
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Input, Button, Label, Select, Checkbox, RadioSet, RadioButton, RichLog
from textual.containers import Vertical, Horizontal
from rich.syntax import Syntax

from ssh_term.theme import get_color
from ssh_term.models.connection import SSHConnection
from ssh_term.models.config import CONFIG_DIR

PLUGIN_DIR = CONFIG_DIR / "plugins"

class BatchExecuteScreen(Screen):
    CSS = """
    BatchExecuteScreen {
        background: $background;
        padding: 1 2;
    }
    BatchExecuteScreen Vertical {
        width: 1fr;
    }
    BatchExecuteScreen Horizontal {
        height: 3;
        align: left middle;
        margin-bottom: 1;
    }
    BatchExecuteScreen .label {
        width: 18;
        text-align: right;
        margin-right: 2;
        text-style: bold;
        color: $primary;
    }
    BatchExecuteScreen Input {
        width: 1fr;
    }
    BatchExecuteScreen Select {
        width: 1fr;
    }
    BatchExecuteScreen RichLog {
        height: 1fr;
        border: solid $panel;
        background: $surface;
        margin-top: 1;
    }
    #title {
        width: 1fr;
        text-align: center;
        margin-bottom: 2;
    }
    #action_value_container {
        height: auto;
        padding: 1 1;
        margin-bottom: 1;
        border: blank $primary;
        background: $surface;
    }
    #action_val_snippet, #action_val_plugin, #action_val_upload {
        display: none;
    }
    #action_val_upload {
        height: auto;
    }
    #exec_mode {
        layout: horizontal;
        border: none;
        height: 1;
        margin-left: 2;
    }
    .btn-row {
        align: center middle;
        margin-bottom: 1;
        margin-top: 1;
    }
    """

    def on_mount(self) -> None:
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        self.log_widget = self.query_one(RichLog)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("🚀 Batch Execution & Plugin Runner", id="title", classes="label")
            
            with Horizontal():
                yield Label("Targets Matcher", classes="label")
                yield Input(placeholder="e.g. .* or 192.168.1.* or prod", id="target_matcher")
                
            with Horizontal():
                yield Label("Action Type", classes="label")
                action_options = [
                    ("Raw Command", "command"),
                    ("Snippet", "snippet"),
                    ("Python Plugin", "plugin"),
                    ("Upload File", "upload")
                ]
                yield Select(options=action_options, id="action_type", value="command")
                
            with Vertical(id="action_value_container"):
                with Horizontal(id="action_val_command"):
                    yield Label("Command", classes="label")
                    yield Input(placeholder="e.g. apt update", id="cmd_input")
                    
                with Horizontal(id="action_val_snippet"):
                    yield Label("Snippet", classes="label")
                    yield Select(options=[], id="snippet_select")
                    
                with Horizontal(id="action_val_plugin"):
                    yield Label("Plugin Script", classes="label")
                    yield Select(options=[], id="plugin_select")
                    
                with Vertical(id="action_val_upload"):
                    with Horizontal():
                        yield Label("Local Path", classes="label")
                        yield Input(placeholder="e.g. /home/user/script.sh", id="upload_local")
                    with Horizontal():
                        yield Label("Remote Dest", classes="label")
                        yield Input(placeholder="e.g. /tmp/", id="upload_remote")

            with Horizontal():
                yield Label("Parameters", classes="label")
                yield Checkbox("Elevate Privileges (sudo)", id="use_sudo")
                with RadioSet(id="exec_mode"):
                    yield RadioButton("Async", value=True)
                    yield RadioButton("Sync")
            
            with Horizontal(classes="btn-row"):
                yield Button("Cancel", id="btn_cancel")
                yield Button("Execute Batch Work", id="btn_execute", variant="primary")
                yield Button("Clear Log", id="btn_clear")
                
            yield self.log_widget if hasattr(self, "log_widget") else RichLog(highlight=True, markup=True)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.control.id == "action_type":
            v = event.value
            self.query_one("#action_val_command").display = (v == "command")
            self.query_one("#action_val_snippet").display = (v == "snippet")
            self.query_one("#action_val_plugin").display = (v == "plugin")
            self.query_one("#action_val_upload").display = (v == "upload")
            
            if v == "snippet":
                snip_sel = self.query_one("#snippet_select", Select)
                options = [(s.name, s.id) for s in self.app.config_manager.snippets]
                snip_sel.set_options(options)
            elif v == "plugin":
                plug_sel = self.query_one("#plugin_select", Select)
                plgs = [f.name for f in PLUGIN_DIR.glob("*.py")]
                options = [(p, p) for p in plgs]
                if not plgs:
                    options = [("No .py files in ~/.config/ssh-term/plugins/", "")]
                plug_sel.set_options(options)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_cancel":
            self.app.pop_screen()
        elif event.button.id == "btn_clear":
            self.query_one(RichLog).clear()
        elif event.button.id == "btn_execute":
            self.run_batch()

    @work
    async def run_batch(self) -> None:
        log = self.query_one(RichLog)
        matcher = self.query_one("#target_matcher", Input).value.strip() or ".*"
        action_type = self.query_one("#action_type", Select).value
        use_sudo = self.query_one("#use_sudo", Checkbox).value
        
        mode_radio = self.query_one("#exec_mode", RadioSet)
        is_async = mode_radio.pressed_index == 0
        
        try:
            regex = re.compile(matcher, re.IGNORECASE)
        except Exception:
            log.write("[bold red]Invalid Regex Matcher[/]")
            return

        matched_conns = []
        for c in self.app.config_manager.connections:
            if regex.search(c.name) or regex.search(c.ip) or regex.search(c.host) or any(regex.search(t) for t in c.tags):
                matched_conns.append(c)

        if not matched_conns:
            log.write(f"[bold yellow]No connections matched '{matcher}'[/]")
            return

        cmd_to_run = ""
        plugin_file = None
        upload_local = ""
        upload_remote = ""

        if action_type == "command":
            cmd_to_run = self.query_one("#cmd_input", Input).value.strip()
            if not cmd_to_run: return
        elif action_type == "snippet":
            s_id = self.query_one("#snippet_select", Select).value
            snip = next((s for s in self.app.config_manager.snippets if s.id == s_id), None)
            if not snip: return
            cmd_to_run = snip.content
        elif action_type == "plugin":
            p_name = self.query_one("#plugin_select", Select).value
            if not p_name: return
            plugin_file = PLUGIN_DIR / p_name
        elif action_type == "upload":
            upload_local = self.query_one("#upload_local", Input).value.strip()
            upload_remote = self.query_one("#upload_remote", Input).value.strip()
            if not upload_local or not upload_remote: return

        if action_type in ("command", "snippet"):
            log.write(Syntax(cmd_to_run, "bash", theme="monokai", word_wrap=True))
            tasks = []
            for c in matched_conns:
                if is_async:
                    task = asyncio.create_task(self.exec_on_conn(c, cmd_to_run, use_sudo))
                    tasks.append(task)
                else:
                    await self.exec_on_conn(c, cmd_to_run, use_sudo)
            if is_async and tasks:
                await asyncio.gather(*tasks)
                
        elif action_type == "plugin":
            log.write(f"[bold cyan]Executing Python Plugin: {p_name}[/]")
            try:
                spec = importlib.util.spec_from_file_location("plugin_mod", str(plugin_file))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "run"):
                    await mod.run(self.app, matched_conns, log.write)
                else:
                    log.write(f"[bold red]Plugin {p_name} has no async def run() function[/]")
            except Exception as e:
                log.write(f"[bold red]Plugin Crash: {e}[/]")
                
        elif action_type == "upload":
            log.write(f"[bold cyan]Initiating Batch Upload: {upload_local} -> {upload_remote}[/]")
            tasks = []
            for c in matched_conns:
                if is_async:
                    task = asyncio.create_task(self.upload_on_conn(c, upload_local, upload_remote))
                    tasks.append(task)
                else:
                    await self.upload_on_conn(c, upload_local, upload_remote)
            if is_async and tasks:
                await asyncio.gather(*tasks)

    async def upload_on_conn(self, conn: SSHConnection, local_path: str, remote_path: str) -> None:
        log = self.query_one(RichLog)
        
        password = None
        if conn.auth_method == "password" and conn.password_encrypted:
            password = self.app.auth_manager.decrypt(conn.password_encrypted)
            
        try:
            if not self.app.ssh_manager.is_connected(conn.id):
                await self.app.ssh_manager.connect(conn, password=password)
            sftp = await self.app.ssh_manager.open_sftp(conn.id)
                
            log.write(f"[dim][{conn.name}] Starting transfer...[/]")
            if Path(local_path).is_dir():
                from ssh_term.services.sftp_manager import SFTPManager
                mgr = SFTPManager(sftp)
                await mgr.upload_recursive(local_path, remote_path)
            else:
                await sftp.put(local_path, remote_path)
            log.write(f"[bold green][{conn.name}] Upload successful![/]")
        except Exception as e:
            log.write(f"[bold red][{conn.name}] Transfer failed: {e}[/]")

    async def exec_on_conn(self, conn: SSHConnection, cmd: str, sudo: bool) -> None:
        log = self.query_one(RichLog)
        log.write(f"[dim]Initiating connection to {conn.name} ({conn.host})...[/]")
        
        password = None
        if conn.auth_method == "password" and conn.password_encrypted:
            password = self.app.auth_manager.decrypt(conn.password_encrypted)
            
        try:
            client = await self.app.ssh_manager.connect(conn, password=password)
        except Exception as e:
            log.write(f"[bold red][{conn.name}] Connection failed: {e}[/]")
            return

        if sudo:
            cmd = f"sudo -S {cmd}"
        
        log.write(f"[bold blue][{conn.name}] Executing...[/]")
        
        try:
            if sudo:
                res = await client.run(cmd, input=f"{password}\n" if password else "")
            else:
                res = await client.run(cmd)
                
            stdout = res.stdout if hasattr(res, 'stdout') else ""
            stderr = res.stderr if hasattr(res, 'stderr') else ""
            
            if stdout:
                log.write(f"[bold green][{conn.name}] STDOUT:[/]")
                log.write(Syntax(str(stdout), "bash", theme="monokai", word_wrap=True))
            if stderr:
                log.write(f"[bold red][{conn.name}] STDERR:[/]")
                log.write(str(stderr))
                
            if not stdout and not stderr:
                log.write(f"[dim][{conn.name}] completed with no output.[/]")
                
        except Exception as e:
            log.write(f"[bold red][{conn.name}] command failed: {e}[/]")
