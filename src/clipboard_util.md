# clipboard_util plugin

This plugin allows to send the existing tui selection to clipboard or file, if the default `copy_to_clipboard()` implementation (see "how tau tui works with selection" section below)  does not work for you.

## plugin configuration example

 * `~/.tau/extensions/clipboard_util/settings.json` :

Note that a real configuration file would likely choke on comments, so I'm adding these only to tag specific remarks in this text )

Please also not that this file is a bit of an overkill, I'm just trying to show various options at once. Pick and choose.

```json
{
    "hotkey": "ctrl+w",
    "commands": [
        [ "xclip", "-i", "-selection", "clipboard" ], // (1)
        "cat - >/tmp/deleteme.txt"                    // (2)
    ],
    "file": {
      "save_path": "/workspace/.tau-clipboard.txt",   // (3)
      "mode": "fallback:overwrite"                    // (4)
    }
}
```

### plugin configuration example - commands

 * `(1)` : "array" form of a command specification will directly invoke it as a subprocess (no shell involved) and pipe the selected text to its `stdin`
 * `(2)` : "text" form of a command will run it through the default system shell, likely `/bin/sh` on a Unix host.

The commands are tried sequentially, until one succeeds or every command fails, in which case we will get _all_ the error messages via a tui "popup". 
If at least one command succeeds, the tui will report only that.

### plugin configuration example - file fallback

In the case if all the commands fail, or there are simply no commands specified -- one can instruct the plugin to save the selected text into a file,
from where it can be retrieved by other means or @-attached:

 * `(3)`: "save_path" option defines the file path to use
 * `(4)`: "mode" is a pair of keywords, separated by a colon `:`
   * the first keyword shall be one of:
     * "fallback" for saving the selection to file only if we exhaust other means 
     * or "shadow" in the case if we want a file copy of the selection irregardless of command success -- one may say that a file copy "shadows" command invocation, hence the keyword.
   * as for the second keyword in the pair, it can be one of "overwrite" or "append" -- which do exactly what they say.


## how tau tui works with selection

If you have `~/tau/tui.json::auto_copy_selection` set to `true` (and `auto_copy_selection` is set to `false` by default), 
then the following piece of code will apply when you select something in `tau` history:

 * `src/tau_coding/tui/app.py`

```python
def copy_to_clipboard(self, text: str) -> None:
    """Copy text using pyperclip when available, then Textual's fallback."""
    if self._supports_pyperclip is None:
        try:
            import pyperclip  # type: ignore[import-untyped]
        except ImportError:
            self._supports_pyperclip = False
        else:
            self._supports_pyperclip = True
    if self._supports_pyperclip:
        import pyperclip

        with suppress(Exception):
            pyperclip.copy(text)
    super().copy_to_clipboard(text)
```

This would [use an OSC 52 ANSI escape sequence](https://darren.codes/posts/textual-copy-paste/) by default -- unless you would install `pyperclip`, in which case it would use its wisdoms.

However, all this still does not guarantee anything, since copy_to_clipboard() return value is usually ignored.

