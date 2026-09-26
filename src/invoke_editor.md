This plugin allows to edit the current prompt in an external editor (`$EDITOR` / `$VISUAL` are tried in addition to plugin settings).

Settings example (settings file is optional):

 * `~/.tau/extensions/invoke_editor/settings.json` :

```json
{
  "hotkey": "f4",
  "editor": "nano"
}
```

If there's no settings file, the default hotkey is `f4`, and the invoked editor will be the first of:
 * `$VISUAL`
 * `$EDTOR`
 * `$default` 
 
.. where `$default` is `vi` or `notepad.exe`, depending on the system.
