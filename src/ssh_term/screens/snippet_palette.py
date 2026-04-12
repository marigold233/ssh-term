"""Command Palette for quick snippet insertion."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Label
from textual.widgets.option_list import Option

from ssh_term.models.snippet import Snippet
from ssh_term.models.config import ConfigManager


class SnippetPaletteScreen(ModalScreen[str]):
    """A modal screen that lets users filter and pick a snippet to inject."""
    
    DEFAULT_CSS = """
    SnippetPaletteScreen {
        align: center middle;
        background: $background 50%;
    }

    #palette-container {
        width: 80%;
        max-width: 80;
        height: 70%;
        border: thick $primary;
        background: $surface;
        padding: 1;
    }
    
    #palette-search {
        margin-bottom: 1;
    }
    
    #palette-list {
        height: 1fr;
        border: solid $panel;
    }
    
    .snippet-title {
        color: $accent;
        text-style: bold;
    }
    
    .snippet-tags {
        color: $secondary;
    }
    
    .snippet-desc {
        color: $foreground 70%;
    }
    """

    def __init__(self, config_manager: ConfigManager) -> None:
        super().__init__()
        self.config_manager = config_manager
        self._all_snippets: list[Snippet] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-container"):
            yield Label("Search Snippets (Press Esc to cancel)", id="palette-label")
            yield Input(placeholder="Type to filter by name, tags or description...", id="palette-search")
            yield OptionList(id="palette-list")

    def on_mount(self) -> None:
        self._all_snippets = self.config_manager.snippets
        self._populate_list()
        self.query_one("#palette-search").focus()

    def _populate_list(self, filter_text: str = "") -> None:
        lst = self.query_one("#palette-list", OptionList)
        lst.clear_options()
        
        filter_text = filter_text.lower()
        
        for snippet in self._all_snippets:
            if filter_text:
                search_target = f"{snippet.name} {snippet.description} {' '.join(snippet.tags)}".lower()
                if filter_text not in search_target:
                    continue
                    
            title_text = f"[b]{snippet.name}[/b]"
            if snippet.tags:
                title_text += f" [dim][{', '.join(snippet.tags)}][/dim]"
            
            prompt = f"{title_text}\n  [italic]{snippet.description or '<No description>'}[/italic]"
            
            # Store the snippet ID as the option ID
            lst.add_option(Option(prompt, id=snippet.id))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "palette-search":
            self._populate_list(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        snippet_id = event.option.id
        for snippet in self._all_snippets:
            if snippet.id == snippet_id:
                # Return the content back to the caller
                self.dismiss(snippet.content)
                return
        self.dismiss("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-search":
            lst = self.query_one("#palette-list", OptionList)
            if lst.option_count > 0:
                # Select the currently highlighted item or the first one
                idx = lst.highlighted if lst.highlighted is not None else 0
                snippet_id = lst.get_option_at_index(idx).id
                for snippet in self._all_snippets:
                    if snippet.id == snippet_id:
                        self.dismiss(snippet.content)
                        return

