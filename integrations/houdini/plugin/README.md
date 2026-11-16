# OpenCode for Houdini (preview)

Read, change and check the scene open in Houdini from any OpenCode conversation: CodeNomad, the terminal, the desktop app, or the **OpenCode Link** panel inside Houdini. Houdini 21 or later and OpenCode 2.2 or later are required; the panel also needs Node.js 22 or later.

## Install

Add the plugin to the **global** OpenCode configuration (in CodeNomad: from the reviewed catalogue, in Plugins):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": [{ "package": "@neuralnomads/opencode-houdini@^0.3" }]
}
```

Then install OpenCode Link, the part that runs inside Houdini: `houdini_install_host` (it asks permission), or CodeNomad's install action. It goes into the newest Houdini 21+ preferences folder found (`Documents/houdini22.0` on Windows, `~/houdini22.0` on Linux, `~/Library/Preferences/houdini/22.0` on macOS); `hostInspectionDir` in the plugin's options names another one. Houdini creates that folder at its first start: start Houdini once before installing. The installation writes `packages/neuralnomads_houdini.json`, which points at a checksummed copy of OpenCode Link under `%LOCALAPPDATA%/NeuralNomads/opencode-houdini/hosts/` (`$XDG_STATE_HOME/neuralnomads/opencode-houdini/hosts/` elsewhere); installing again is idempotent. WSL installation is refused.

Restart Houdini: OpenCode Link starts with Houdini's interface and offers it to OpenCode every 5 seconds, labelled with the open file (`Houdini: shot.hipnc`). `OPENCODE_HOUDINI_OFFER=0` in Houdini's environment keeps it from offering itself. Background `hython` never offers itself.

An earlier OpenCode installation (`packages/opencode_houdini.json` in a Houdini preferences folder) loads another panel and host next to OpenCode Link: `houdini_hosts` and CodeNomad's status say so. Remove that file, or set `"enable": false` in it, then restart Houdini.

## Use

The first Houdini tool call of a conversation attaches the only Houdini offered when no other conversation uses it; otherwise `houdini_hosts` lists the Houdinis offered and `houdini_hosts attach` attaches one (permission `houdini_attach`). An attachment is kept across an OpenCode restart and when Houdini opens another file. A working method joins the conversation when it first uses a Houdini.

| Tool                   |                                                                                                                                                                                                        |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `houdini_hosts`        | The Houdinis offered, which conversation holds each; `attach`, `detach`.                                                                                                                               |
| `houdini_context`      | A bounded read: revision, open file, selection, node cards; `release_quarantine` after a failed change.                                                                                                |
| `houdini_execute_code` | Python in Houdini (`hou`), as code or a script file; permission `houdini_execute_code` on every run.                                                                                                   |
| `houdini_inspect`      | Search node types, parms and `hou` symbols; a type with its parm templates and its SideFX documentation link; a live node (parms, errors, geometry, USD stage, work items).                            |
| `houdini_verify`       | Measured criteria, node errors and warnings, an optional low-resolution render; `saved: true` reads the saved file in a background `hython`.                                                           |
| `houdini_save`         | Save the open file, or a copy that leaves the open file's name.                                                                                                                                        |
| `houdini_render`       | A ROP render or a PDG cook in a background `hython`, followed as a task (`task`, `wait_s`, `cancel`).                                                                                                  |
| `houdini_apply`        | Typed graph edits (`node.create`, `node.rename`, `parm.set_value`, `graph.connect`, `graph.delete`); dry run by default, changes with `confirm` and permission `houdini_write`, in the interface only. |
| `houdini_install_host` | Install or update OpenCode Link.                                                                                                                                                                       |

A code run that changes the scene is one undo group (`OpenCode: code`); when it fails, the group is undone and the node graph compared with what it was. When that cannot be proven, changes are refused (reads and checks still run) until the agent reads the scene and releases the quarantine with that read's revision. A failure says what Houdini knows: the closest node types in the parent's network, the real parm names when `parm()` returned `None`, the errors of the nodes that failed to cook, a function's signature, the closest attributes.

A call that outlasts its wait says whether Houdini still runs it (OpenCode Link answers `status` off Houdini's main thread). When Houdini crashes, the call running then stops within two seconds and says so, with the files Houdini wrote in `houdini_temp`: the scene as it was at the crash (`crash.<file>.<user>_<pid>.hipnc`), from which the changes can be recovered, or its crash log. A Houdini closed, or alive without renewing its offer, is said as such, and no other Houdini attaches itself in its place.

## The OpenCode Link panel

The **OpenCode Link** Python panel is a conversation inside Houdini. **Connect** attaches the panel's conversation to this Houdini's own offer, taking back the conversation it had for this file; **New** starts another. Closing the panel releases its conversation only: Houdini stays offered and other conversations attached to it keep it. Both write to the same scene, each change checked against its revision. Node.js is found on `PATH`; `NEURALNOMADS_HOUDINI_NODE` names another one.

## Limits

- Houdini Apprentice: files are non-commercial, renders are at most 1920×1080 and carry the Houdini logo; render results say so.
- `houdini_apply` refuses changes in `hython` (no interface); `houdini_execute_code` works in both. Houdini records undo only on its main thread: OpenCode Link runs every request there (in `hython`, a script serves them with `opencode_houdini.pump()`).
- `hou.hipFile.hasUnsavedChanges()` stays true after a save in `hython`: OpenCode Link judges a save by the file's date on disk.

## Safety

OpenCode Link listens on `127.0.0.1` only, on an ephemeral port, and authenticates every request with a process-random 256-bit token that lives in memory and private pipes only. Credentials, ports and document handles stay out of status outputs and events. Loading the plugin opens no socket and starts no process.

## Tests

System Python runs OpenCode Link's listener, code runs, undo, quarantine and `status` with a stub `hou`, and the hints against a stub. With `HOUDINI_TEST_BIN` naming `hython` (or Houdini's `bin` folder), `test/programs_hython.py` runs the programs, the hints and OpenCode Link itself in a real Houdini, with private preferences, packages and temp folders.

Publication uses the repository's manually approved OIDC workflow and `houdini-v<version>` tags.
