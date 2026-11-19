# OpenCode for Gaffer (preview)

Read, change and check the scripts open in Gaffer from any OpenCode conversation: CodeNomad, the terminal, the desktop app, or the **OpenCode for Gaffer** panel inside Gaffer. Gaffer 1.6 or later and OpenCode 2.2 or later are required.

## Install

Add the plugin to the **global** OpenCode configuration (in CodeNomad: from the reviewed catalogue, in Plugins):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": [{ "package": "@neuralnomads/opencode-gaffer@^0.3" }]
}
```

Then install OpenCode Link, the part that runs inside Gaffer: `gaffer_install_host` (it asks permission), or CodeNomad's install action. It writes the loader `neuralnomadsOpenCode.py` into Gaffer's user startup folder (`~/gaffer/startup/gui`; `hostStartupDir` in the plugin's options names another one), which starts a checksummed copy of OpenCode Link under `%LOCALAPPDATA%/NeuralNomads/opencode-gaffer/hosts/` (`$XDG_STATE_HOME/neuralnomads/opencode-gaffer/hosts/` elsewhere). Installing again is idempotent; an existing loader not written by this integration is left untouched and the installation refused. To remove it, delete that loader.

Restart Gaffer: OpenCode Link starts with Gaffer's interface and offers each open script to OpenCode every 5 seconds, labelled with its file (`Gaffer: shot.gfr`; two scripts with the same file are numbered). `OPENCODE_GAFFER_OFFER=0` in Gaffer's environment keeps it from offering them. A background `gaffer python` (verification, renders) never offers itself.

An earlier OpenCode installation (`~/gaffer/startup/gui/openCode.py` and the `~/gaffer/OpenCode` folder) registers another editor and loads a package of the same name next to OpenCode Link: `gaffer_hosts` and CodeNomad's status say so. Remove them, then restart Gaffer.

## Use

The first Gaffer tool call of a conversation attaches the only script offered when no other conversation uses it; otherwise `gaffer_hosts` lists the scripts offered and `gaffer_hosts attach` attaches one (permission `gaffer_attach`). An attachment is kept across an OpenCode restart. A working method joins the conversation when it first uses a Gaffer script.

| Tool                  |                                                                                                                                                            |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `gaffer_hosts`        | The scripts offered, which conversation holds each; `attach`, `detach`.                                                                                    |
| `gaffer_context`      | A bounded read: revision, file, selection, focus, node cards; `release_quarantine` after a failed change.                                                  |
| `gaffer_execute_code` | Python in Gaffer on the attached script (`script`, `Gaffer`, `GafferScene`, ...), as code or a script file; permission `gaffer_execute_code` on every run. |
| `gaffer_inspect`      | Search node classes, plugs and descriptions; a class with its plugs; a live node (plugs, values, errors, scene); a Python symbol.                          |
| `gaffer_verify`       | Measured criteria, computation errors; `saved: true` reads the saved file in a background `gaffer python`.                                                 |
| `gaffer_save`         | Save the script, or a copy that leaves the script's file name.                                                                                             |
| `gaffer_render`       | A task node (a render, an image writer) executed by a background `gaffer python`, followed as a task (`task`, `wait_s`, `cancel`).                         |
| `gaffer_apply`        | Typed graph edits (`node.create`, `node.rename`, `plug.set_value`, `graph.connect`, `graph.delete`, ...) as one undo step, permission `gaffer_write`.      |
| `gaffer_install_host` | Install or update OpenCode Link.                                                                                                                           |

A code run that changes the script is one undo step (`OpenCode: code`); when it fails, the step is undone and the script compared with what it was. When that cannot be proven, changes are refused (reads and checks still run) until the agent reads the script and releases the quarantine with that read's revision. A failure says what Gaffer knows: the closest plug or child names, the closest classes of a module, the value type a plug's `setValue` takes, the plug a computation failed on with the node's expression or file, the types of two plugs that cannot connect.

A call that outlasts its wait says whether Gaffer still runs it (OpenCode Link answers `status` off Gaffer's main thread). Gaffer writes no crash report: a Gaffer that closed or crashed is said as closed, one alive without renewing its offers as no longer offering them, and no other Gaffer attaches itself in its place.

## The OpenCode for Gaffer panel

The **OpenCode for Gaffer** editor connects automatically to the latest conversation in the script’s folder. Opening another version in that folder keeps the selected conversation. The selector next to the model lists the folder’s conversations; **New conversation** creates nothing until you send a message. **Stop** interrupts the turn or clears queued prompts. The panel uses OpenCode’s executable, with Node.js 22+ (`NEURALNOMADS_GAFFER_NODE` or `PATH`) as a fallback.

## Limits

- Gaffer records undo only on its main thread: OpenCode Link runs every request there through Gaffer's event loop (in `gaffer python`, a script serves them with `GafferOpenCode.pump()`). A request the main thread has not taken within 10 s, because another one still runs there, is answered with what runs and since when; nothing is done for it.
- `gaffer_verify` with `saved` only starts its background `gaffer python` from Gaffer: the file is read while Gaffer stays free, `timeout_s` bounds the read and a stopped read says what it was doing.
- Node and plug descriptions come from Gaffer's UI modules, which `gaffer_inspect` imports (no window opens).

## Safety

OpenCode Link listens on `127.0.0.1` only, on an ephemeral port, and authenticates every request with a process-random 256-bit token that lives in memory and private pipes only. Credentials, ports and script handles stay out of status outputs and events. Loading the plugin opens no socket and starts no process.

## Tests

`node --test integrations/gaffer/test/*.test.ts` runs the plugin, the panel's sidecar and the listener under a stub application. With `GAFFER_TEST_BIN` naming Gaffer's launcher (`gaffer.cmd` on Windows), `test/python/real_host.py` runs OpenCode Link in a real Gaffer (its event loop offscreen: code runs, undo, hints, quarantine, the busy answer, the offers) and `test/programs_gaffer.py` the plugin's programs, each with private HOME, USERPROFILE and temp folders.

Publication uses the repository's manually approved OIDC workflow and `gaffer-v<version>` tags.
