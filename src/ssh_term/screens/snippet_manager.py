"""Snippet Management Screen for CRUD operations."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListView, ListItem, TextArea
from textual.binding import Binding

from ssh_term.models.snippet import Snippet
from ssh_term.theme import get_color, TERMINAL_BG


class SnippetItem(ListItem):
    """List item for a snippet."""
    
    def __init__(self, snippet: Snippet, **kwargs) -> None:
        super().__init__(**kwargs)
        self.snippet = snippet

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"[b]{self.snippet.name}[/b]", classes="snippet-title")
            if self.snippet.tags:
                yield Label(f"[dim]{', '.join(self.snippet.tags)}[/dim]", classes="snippet-tags")


class SnippetManagerScreen(Screen):
    """Screen for managing snippets."""

    BINDINGS = [
        ("escape,ctrl+b", "back_to_dash", "Back"),
        ("ctrl+n", "new_snippet", "New Snippet"),
        ("ctrl+s", "save_snippet", "Save"),
        ("ctrl+d", "delete_snippet", "Delete"),
    ]
    
    CSS = """
    SnippetManagerScreen {
        background: $surface;
    }
    
    #snippet-layout {
        height: 100%;
    }
    
    #snippet-sidebar {
        width: 30%;
        height: 100%;
        border-right: solid $panel;
        background: $background;
    }
    
    #snippet-editor {
        width: 70%;
        height: 100%;
        padding: 1 2;
    }
    
    #snippet-list {
        height: 1fr;
    }
    
    .snippet-title {
        color: $accent;
    }
    
    .editor-label {
        margin-top: 1;
        color: $secondary;
        text-style: bold;
    }
    
    #content-input {
        height: 1fr;
        min-height: 10;
        border: solid $panel;
        background: """ + TERMINAL_BG + """;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._current_snippet: Snippet | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="snippet-layout"):
            with Vertical(id="snippet-sidebar"):
                yield Label(" Snippets Library", classes="editor-label")
                yield ListView(id="snippet-list")
                with Horizontal(classes="buttons-row"):
                    yield Button("New (+)", id="btn-new", variant="success")
                    yield Button("Delete", id="btn-delete", variant="error")
            
            with Vertical(id="snippet-editor"):
                yield Label("Name (Short alias)", classes="editor-label")
                yield Input(id="name-input", placeholder="e.g. restart-docker")
                
                yield Label("Tags (Comma separated)", classes="editor-label")
                yield Input(id="tags-input", placeholder="e.g. devops, docker, web")
                
                yield Label("Description (Remarks)", classes="editor-label")
                yield Input(id="desc-input", placeholder="Restarts all web containers")
                
                yield Label("Content (Bash script or command)", classes="editor-label")
                yield TextArea(language="bash", id="content-input")
                
                yield Button("Save Snippet", id="btn-save", variant="primary")
        
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_list()

    def _refresh_list(self, select_id: str | None = None) -> None:
        lst = self.query_one("#snippet-list", ListView)
        lst.clear()
        
        snippets = self.app.config_manager.snippets
        for snippet in snippets:
            lst.append(SnippetItem(snippet))
        
        if snippets and not select_id:
            lst.index = 0
            self._load_snippet(snippets[0])
        elif snippets and select_id:
            for i, snippet in enumerate(snippets):
                if snippet.id == select_id:
                    lst.index = i
                    self._load_snippet(snippet)
                    break
        else:
            self.action_new_snippet()

    def _load_snippet(self, snippet: Snippet | None) -> None:
        self._current_snippet = snippet
        if not snippet:
            self.query_one("#name-input", Input).value = ""
            self.query_one("#tags-input", Input).value = ""
            self.query_one("#desc-input", Input).value = ""
            self.query_one("#content-input", TextArea).text = ""
            return
            
        self.query_one("#name-input", Input).value = snippet.name
        self.query_one("#tags-input", Input).value = ", ".join(snippet.tags)
        self.query_one("#desc-input", Input).value = snippet.description
        self.query_one("#content-input", TextArea).text = snippet.content

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, SnippetItem):
            self._load_snippet(event.item.snippet)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-new":
            self.action_new_snippet()
        elif event.button.id == "btn-save":
            self.action_save_snippet()
        elif event.button.id == "btn-delete":
            self.action_delete_snippet()

    def action_new_snippet(self) -> None:
        self.query_one("#snippet-list", ListView).index = None
        self._load_snippet(None)
        self.query_one("#name-input").focus()

    def action_save_snippet(self) -> None:
        name = self.query_one("#name-input", Input).value.strip()
        if not name:
            self.app.notify("Snippet name is required!", severity="error")
            return
            
        tags_raw = self.query_one("#tags-input", Input).value
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        
        desc = self.query_one("#desc-input", Input).value.strip()
        content = self.query_one("#content-input", TextArea).text
        
        if self._current_snippet:
            # Update existing
            self._current_snippet.name = name
            self._current_snippet.tags = tags
            self._current_snippet.description = desc
            self._current_snippet.content = content
            self.app.config_manager.update_snippet(self._current_snippet)
            self.app.notify("Snippet updated!")
            self._refresh_list(self._current_snippet.id)
        else:
            # Create new
            snippet = Snippet(name=name, tags=tags, description=desc, content=content)
            self.app.config_manager.add_snippet(snippet)
            self.app.notify("Snippet created!")
            self._refresh_list(snippet.id)

    def action_delete_snippet(self) -> None:
        if not self._current_snippet:
            return
        self.app.config_manager.delete_snippet(self._current_snippet.id)
        self.app.notify("Snippet deleted!")
        self._refresh_list()

    def action_back_to_dash(self) -> None:
        self.app.switch_screen("dashboard")
