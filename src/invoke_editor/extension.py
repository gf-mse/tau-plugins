"""invoke_editor/extension.py -- v2: borrows two improvements from the embedded implementation.

v1 (`examples/v1/invoke_editor/extension.py`) was compared against the
core-Tau (non-plugin) implementation of the same feature, completed on git
branch `invoke-external-editor-hotkey-v2` (see
`notes/plugins-howto/invoke_editor.v1.md` for the full comparison). Two of
that implementation's choices are worth borrowing here:

1. **`$VISUAL` before `$EDITOR`.** The classic Unix convention (git,
   crontab, ...) checks `$VISUAL` first, since it names a full-screen editor
   specifically, falling back to `$EDITOR` (which may be a line editor).
   v1 only checked `$EDITOR`, per the task's literal wording; v2 adds the
   `$VISUAL` check ahead of it (an explicit `editor` setting still wins over
   both, which is v1's own addition the embedded side does not have).
2. **`sync_pending_paste()` after writing the prompt back.** Tau's
   `PromptInput` tracks pasted-content placeholders; if the user deletes one
   while editing externally, this call drops the now-stale mapping so a
   later submit doesn't re-expand a placeholder that no longer appears in
   the visible text. v1 omitted this.

Everything else -- the hotkey-interceptor wiring, the `getattr(components,
"_app", None)` reach for suspend/resume and the prompt widget, the
documented `components.get_prompt_text()` read, the settings file shape --
is unchanged from v1.

Install by copying this whole directory into ``~/.tau/extensions/``, or run:

    tau -e notes/plugins-howto/examples/v2/invoke_editor

Settings (optional) live right next to this file, at
``invoke_editor/invoke-editor.settings.json``:

    {
      "hotkey": "f4",
      "editor": "code --wait"
    }

``editor`` overrides everything else when set; omit it (or the whole
settings file) to use ``$VISUAL``, then ``$EDITOR``, falling back to ``vi``
(``notepad`` on Windows) if neither is set.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from textual.app import SuspendNotSupported
from textual.css.query import NoMatches

from tau_coding.extensions import ExtensionAPI, ExtensionContext

_SETTINGS_FILE_NAME = "settings.json"
_DEFAULT_HOTKEY = "f4" # or "ctrl-g", which seems to be the default hotkey for many harnesses
_EXTENSION_DIR = Path(__file__).resolve().parent


def _settings_path() -> Path:
    return _EXTENSION_DIR / _SETTINGS_FILE_NAME


def _load_settings(context: ExtensionContext) -> dict[str, Any]:
    """Load `{hotkey, editor}` from the settings file, defaults on any miss."""
    settings: dict[str, Any] = {"hotkey": _DEFAULT_HOTKEY, "editor": None}
    path = _settings_path()
    if not path.is_file():
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        context.ui.notify(f"invoke-editor: could not read {path}: {exc}", "warning")
        return settings
    if not isinstance(data, dict):
        return settings

    hotkey = data.get("hotkey")
    if isinstance(hotkey, str) and hotkey.strip():
        settings["hotkey"] = hotkey.strip()

    editor = data.get("editor")
    if isinstance(editor, str) and editor.strip():
        settings["editor"] = editor.strip()
    return settings


def _resolve_editor_command(explicit_editor: str | None) -> list[str]:
    """Return the argv prefix for the editor to run.

    An explicit ``editor`` setting wins outright (v2's own addition, not
    present in the embedded implementation); otherwise checks ``$VISUAL``
    then ``$EDITOR`` (borrowed from the embedded implementation's tested
    convention), then a last-resort platform default. Values may include
    arguments (e.g. ``"code --wait"``) and are split with shell rules so
    quoting still works.
    """
    candidates = (explicit_editor, os.environ.get("VISUAL", ""), os.environ.get("EDITOR", ""))
    for raw in candidates:
        if raw and raw.strip():
            parts = shlex.split(raw.strip(), posix=(os.name != "nt"))
            if parts:
                return parts
    return ["notepad"] if os.name == "nt" else ["vi"]


def _edit_text_in_external_editor(initial_text: str, editor_command: list[str]) -> str:
    """Open `initial_text` in `editor_command` and return the saved text.

    Blocks until the editor process exits (the caller is expected to run
    this off the event loop thread, and to have already suspended the TUI).
    Raises RuntimeError if the editor cannot be started or exits nonzero.
    """
    with tempfile.TemporaryDirectory(prefix="tau-plugin-editor-") as tmp_dir:
        tmp_path = Path(tmp_dir) / "prompt.md"
        tmp_path.write_text(initial_text, encoding="utf-8")
        try:
            completed = subprocess.run([*editor_command, str(tmp_path)], check=False)  # noqa: S603
        except OSError as exc:
            raise RuntimeError(f"could not start editor {editor_command[0]!r}: {exc}") from exc
        if completed.returncode != 0:
            raise RuntimeError(
                f"editor {editor_command[0]!r} exited with status {completed.returncode}"
            )
        return tmp_path.read_text(encoding="utf-8").rstrip("\n")


async def _open_editor_flow(tau: ExtensionAPI, state: dict[str, Any]) -> None:
    """Guard, suspend the TUI, run the editor, and write the result back."""
    if tau.context.is_running:
        tau.notify("invoke-editor: Tau is busy right now; try again once it's idle.", "warning")
        return

    components = tau.context.ui.components
    # UNSUPPORTED, deliberately flagged: no documented API exposes the live
    # TUI app instance. See invoke_editor.v1.md "Decision 2".
    app = getattr(components, "_app", None)
    if app is None:
        tau.notify("invoke-editor: could not reach the live TUI app", "error")
        return
    try:
        prompt_widget = app.query_one("#prompt")
    except NoMatches:
        tau.notify("invoke-editor: could not find the prompt widget", "error")
        return

    original_text = components.get_prompt_text()  # documented: ComponentBridge.get_prompt_text()
    editor_command = _resolve_editor_command(state["editor"])

    try:
        with app.suspend():
            edited_text = await asyncio.to_thread(
                _edit_text_in_external_editor, original_text, editor_command
            )
    except SuspendNotSupported:
        tau.notify("invoke-editor: suspending the terminal is not supported here", "error")
        return
    except RuntimeError as exc:
        tau.notify(f"invoke-editor: {exc}", "error")
        return

    prompt_widget.text = edited_text
    # Borrowed from the embedded implementation: drop any paste placeholder
    # the user deleted while editing externally, so a later submit doesn't
    # re-expand text that no longer appears in the visible prompt.
    sync_pending_paste = getattr(prompt_widget, "sync_pending_paste", None)
    if callable(sync_pending_paste):
        sync_pending_paste()
    document = getattr(prompt_widget, "document", None)
    if document is not None:
        prompt_widget.move_cursor(document.end)
    prompt_widget.focus()


def setup(tau: ExtensionAPI) -> None:
    """Load settings, then register the hotkey once a UI is attached."""
    state: dict[str, Any] = {"hotkey": _DEFAULT_HOTKEY, "editor": None}
    unsubscribe: list[Any] = []

    def on_key(event: Any, prompt_text: str) -> bool:
        del prompt_text
        # Self-gate: only ever consume the exact configured hotkey.
        if event.key != state["hotkey"]:
            return False
        asyncio.get_running_loop().create_task(_open_editor_flow(tau, state))
        return True

    def on_session_start(event: object, context: ExtensionContext) -> None:
        del event
        settings = _load_settings(context)
        state["hotkey"] = settings["hotkey"]
        state["editor"] = settings["editor"]

        components = context.ui.components
        if not components.supports_components:
            return  # print mode / a host without the component seam: stay quiet
        unsubscribe.append(components.register_key_interceptor(on_key))

    def on_session_shutdown(event: object, context: ExtensionContext) -> None:
        del event, context
        while unsubscribe:
            unsubscribe.pop()()

    tau.on("session_start", on_session_start)
    tau.on("session_shutdown", on_session_shutdown)
