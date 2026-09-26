"""clipboard_util/extension.py -- v3: a file fallback for when X is unavailable.

Builds on the v2 directory-form plugin (`examples/v2/clipboard_util/extension.py`):
same hotkey-triggered `active_screen.get_selected_text()` -> external
clipboard command chain (`tui/app.py:4744-4756` is the call being mirrored;
`.venv/.../textual/app.py:1770-1786` is the OSC-52 `copy_to_clipboard` this
whole plugin family replaces). New in v3: every `commands` entry
(`xclip`/`xsel`/`pbcopy`/...) needs a running X11/Wayland session to succeed
at all -- on a host with none (a bare SSH box, a container, a headless CI
runner), every one of them fails and the selection is simply lost. v3 adds a
plain-file fallback for exactly that case, configured through a "file"
section alongside "commands" in the same settings file. See
`clipboard_util.v3.md` (in `notes/plugins-howto/`) for the full design
writeup.

Install by copying this whole directory into ``~/.tau/extensions/``, or run:

    tau -e notes/plugins-howto/examples/v3/clipboard_util

Settings (optional) live right next to this file, at
``clipboard_util/settings.json``:

    {
      "hotkey": "ctrl+y",
      "commands": ["pbcopy"],
      "file": {"save_path": "~/.tau-clipboard.txt", "mode": "fallback:overwrite"}
    }

``file.mode`` is ``"<trigger>:<write>"``:

- ``trigger`` is ``"fallback"`` (write to the file only when every command in
  ``commands`` failed) or ``"shadow"`` (always write to the file, in
  addition to whatever the commands did).
- ``write`` is ``"overwrite"`` (replace the file's contents) or ``"append"``
  (add a line and keep prior contents).

The four combinations: ``fallback:overwrite`` (the default -- write only on
total command failure, replacing the file each time), ``fallback:append``
(write only on total command failure, keeping a running log),
``shadow:overwrite`` (always write, latest selection only), ``shadow:append``
(always write, a running log of every copy regardless of command success).

The file fallback is **on by default** even with no settings file at all --
that is the point of v3: a host with no X subsystem gets a working "copy" the
first time it's used, with no configuration step required first.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from tau_coding.extensions import ExtensionAPI, ExtensionContext

_SETTINGS_FILE_NAME = "settings.json"
_DEFAULT_HOTKEY = "ctrl+y"

# This file's own directory -- resolved once at import time, so the settings
# file travels with the plugin wherever it's installed (v2's improvement,
# unchanged here).
_EXTENSION_DIR = Path(__file__).resolve().parent

# Tried in this exact order, after any commands loaded from the settings
# file. Each is a fixed argv tuple: no shell, the selection text is fed on
# stdin, never interpolated into the command line.
_DEFAULT_COMMAND_PRESETS: tuple[tuple[str, ...], ...] = (
    ("xclip", "-selection", "clipboard"),
    ("xsel", "--clipboard", "--input"),
)

_VALID_TRIGGERS = ("fallback", "shadow")
_VALID_WRITE_MODES = ("overwrite", "append")
_DEFAULT_FILE_SETTINGS: dict[str, str] = {
    "save_path": "~/.tau-clipboard.txt",
    "trigger": "fallback",
    "write": "overwrite",
}


def _settings_path() -> Path:
    """Return the settings file's path: always next to this module."""
    return _EXTENSION_DIR / _SETTINGS_FILE_NAME


def _parse_file_mode(raw_mode: object, context: ExtensionContext) -> tuple[str, str] | None:
    """Parse `"<trigger>:<write>"`; return `None` (defaults win) if invalid."""
    if not isinstance(raw_mode, str):
        return None
    parts = raw_mode.split(":")
    if len(parts) == 2 and parts[0] in _VALID_TRIGGERS and parts[1] in _VALID_WRITE_MODES:
        return parts[0], parts[1]
    context.ui.notify(
        f"clipboard-util: invalid `file.mode` {raw_mode!r}"
        f" (expected '<fallback|shadow>:<overwrite|append>'); using defaults",
        "warning",
    )
    return None


def _load_settings(context: ExtensionContext) -> dict[str, Any]:
    """Load `{hotkey, commands, file}` from the settings file, defaults on any miss."""
    settings: dict[str, Any] = {
        "hotkey": _DEFAULT_HOTKEY,
        "commands": [],
        "file": dict(_DEFAULT_FILE_SETTINGS),
    }
    path = _settings_path()
    if not path.is_file():
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        context.ui.notify(f"clipboard-util: could not read {path}: {exc}", "warning")
        return settings
    if not isinstance(data, dict):
        return settings

    hotkey = data.get("hotkey")
    if isinstance(hotkey, str) and hotkey.strip():
        settings["hotkey"] = hotkey.strip()

    raw_commands = data.get("commands")
    if isinstance(raw_commands, list):
        for entry in raw_commands:
            if isinstance(entry, str) and entry.strip():
                ## settings["commands"].append(tuple(shlex.split(entry)))
                settings["commands"].append(entry)
            elif isinstance(entry, list) and entry and all(isinstance(p, str) for p in entry):
                settings["commands"].append(tuple(entry))

    raw_file = data.get("file")
    if isinstance(raw_file, dict):
        save_path = raw_file.get("save_path")
        if isinstance(save_path, str) and save_path.strip():
            settings["file"]["save_path"] = save_path.strip()
        if "mode" in raw_file:
            parsed_mode = _parse_file_mode(raw_file.get("mode"), context)
            if parsed_mode is not None:
                settings["file"]["trigger"], settings["file"]["write"] = parsed_mode
    elif raw_file is not None:
        context.ui.notify(
            "clipboard-util: `file` setting must be an object; using defaults", "warning"
        )

    return settings


def _command_presets(settings: dict[str, Any]) -> tuple[tuple[str, ...], ...]:
    """Configured commands first, then the built-in xclip/xsel fallback chain."""
    return tuple(settings["commands"]) + _DEFAULT_COMMAND_PRESETS


def _attempt_copy_presets(
    text: str, presets: tuple[tuple[str, ...], ...]
) -> tuple[str | None, list[str]]:
    """Try each preset via a subprocess pipe in order; stop at the first success.

    Returns `(used_command_name, failure_reasons)`. `used_command_name` is
    `None` if every preset failed (never raises -- v3's file fallback needs
    to run afterward either way).
    """
    failures: list[str] = []
    strcmd = False
    for command in presets:
        try:
            strcmd = True if isinstance(command, str) else False
            completed = subprocess.run(  # noqa: S603 - fixed argv, text piped via stdin only
                command,
                input=text,
                text=True,
                capture_output=True,
                timeout=2,
                check=False,
                shell=strcmd
            )
        except FileNotFoundError:
            failures.append(f"{command[0]}: not installed")
            continue
        except OSError as exc:
            failures.append(f"{command[0]}: {exc}")
            continue
        if completed.returncode == 0:
            ## if strcmd:
            ##     # tag = tuple(shlex.split(entry))[0]
            tag = command if strcmd else command[0]
            return tag, failures
        failures.append(f"{command[0]}: exited {completed.returncode} ({completed.stderr.strip()})")
    return None, failures


def _save_to_file(text: str, save_path: str, write_mode: str) -> Path:
    """Write (or append) `text` to `save_path`, creating parent dirs as needed."""
    path = Path(save_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if write_mode == "append":
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
    else:
        path.write_text(text, encoding="utf-8")
    return path


def setup(tau: ExtensionAPI) -> None:
    """Load settings, then register the hotkey once a UI is attached."""
    state: dict[str, Any] = {
        "presets": _DEFAULT_COMMAND_PRESETS,
        "hotkey": _DEFAULT_HOTKEY,
        "file": dict(_DEFAULT_FILE_SETTINGS),
        "app": None,
    }
    unsubscribe: list[Any] = []

    def on_key(event: Any, prompt_text: str) -> bool:
        del prompt_text
        # Self-gate: only ever consume the exact configured hotkey (every
        # other main-screen key must pass through untouched -- see
        # extensions/api.py:262-278).
        if event.key != state["hotkey"]:
            return False

        app = state["app"]
        if app is None:
            tau.notify("clipboard-util: could not reach the live TUI app", "error")
            return True

        # This is literally `active_screen.get_selected_text()` from
        # `TauTuiApp.on_text_selected` (`tui/app.py:4744-4756`) -- the same
        # call Tau's own OSC-52 copy path makes, just triggered by our own
        # hotkey instead of a mouse-selection event.
        selection = app.screen.get_selected_text()
        if not selection:
            tau.notify("clipboard-util: nothing is selected", "warning")
            return True

        used, failures = _attempt_copy_presets(selection, state["presets"])
        file_settings = state["file"]
        trigger, write_mode, save_path = (
            file_settings["trigger"],
            file_settings["write"],
            file_settings["save_path"],
        )
        # "fallback" only writes the file when every command above failed;
        # "shadow" always writes it, on top of whatever the commands did.
        should_write_file = trigger == "shadow" or used is None

        parts: list[str] = []
        level = "info"
        if used is not None:
            parts.append(f"copied via `{used}`")
        else:
            level = "warning"
            parts.append("all clipboard commands failed (" + "; ".join(failures) + ")")

        if should_write_file:
            try:
                saved_path = _save_to_file(selection, save_path, write_mode)
            except OSError as exc:
                parts.append(f"could not save to {save_path}: {exc}")
                level = "error"
            else:
                parts.append(f"saved to {saved_path} ({trigger}:{write_mode})")

        tau.notify("clipboard-util: " + "; ".join(parts), level)

        with open('/tmp/.tau-clipboard.txt', 'wt') as f:
            f.write("selection:\n" + selection)

        return True

    def on_session_start(event: object, context: ExtensionContext) -> None:
        del event
        settings = _load_settings(context)
        state["hotkey"] = settings["hotkey"]
        state["presets"] = _command_presets(settings)
        state["file"] = settings["file"]

        components = context.ui.components
        if not components.supports_components:
            return  # print mode / a host without the component seam: stay quiet

        # UNSUPPORTED, deliberately flagged: there is no documented API that
        # hands an extension the live TUI app instance. `components` is the
        # real `_TuiExtensionUiBridge` (`tui/app.py:270`) behind the
        # `ComponentBridge` protocol type, and that object happens to keep
        # the app on a private `_app` attribute. See clipboard_util.v1.md
        # "Design decision 2" for why this is used anyway, and why the
        # `getattr(..., None)` fallback makes the failure mode "the hotkey
        # quietly does nothing" rather than a crash if this ever breaks.
        state["app"] = getattr(components, "_app", None)

        unsubscribe.append(components.register_key_interceptor(on_key))

    def on_session_shutdown(event: object, context: ExtensionContext) -> None:
        del event, context
        state["app"] = None
        while unsubscribe:
            unsubscribe.pop()()

    tau.on("session_start", on_session_start)
    tau.on("session_shutdown", on_session_shutdown)
