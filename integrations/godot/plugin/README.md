# OpenCode for Godot (preview)

Read, edit and check the project open in Godot from an OpenCode conversation. Requires Godot 4.7 or later in the 4.x series and OpenCode 2.2 or later. The optional editor dock needs Node.js 22 or later; discovery and the editor tools do not.

## Install

Add this entry to the `plugins` array in OpenCode's global `opencode.json(c)` after the package is published:

```json
{ "package": "@neuralnomads/opencode-godot@^0.1" }
```

To try an unpublished build, run `npm ci` and `npm run build --workspace @neuralnomads/opencode-godot` from its checkout. Instead of the npm name, set `package` to the absolute path of `integrations/godot/plugin` (use forward slashes in JSON on Windows).

With the Godot editor closed, call `godot_install_host` from a conversation in the project folder, or pass `project` as the absolute path to the folder containing `project.godot`. Accept the installation permission. Godot reads enabled editor plugins from each project's settings; there is no global editor-plugin installation. Open the project: the add-on is now enabled and advertises the editor without opening the dock. To remove it, disable **OpenCode Link** under **Project → Project Settings → Plugins**, then delete `addons/opencode_link`.

## Use

`godot_hosts` lists editors (`Godot: <project> — <scene>`). The first tool call can attach the only compatible unheld editor. Otherwise pass its `offerID` to `godot_hosts attach`. Tools address the edited scene of that editor; other scene tabs are listed by `godot_context`.

| Tool                 | Purpose                                                                                                                                                                                                                                                  |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `godot_context`      | Read the project, scene, selection, nodes and revision.                                                                                                                                                                                                  |
| `godot_inspect`      | Query the running editor's ClassDB, a live node or the scene tree.                                                                                                                                                                                       |
| `godot_execute_code` | Run a GDScript body or `func run()` in the editor. Assign data to `result`; `scene` is the edited root. Requires permission.                                                                                                                             |
| `godot_verify`       | Compare measured values, e.g. `{"expr":"node(\"Player\").position.x","op":">","value":0}`. `saved: true` reads the file in a separate headless Godot; `frames` runs the scene before measuring. Expressions and background execution require permission. |
| `godot_save`         | Save the edited scene or a copy.                                                                                                                                                                                                                         |
| `godot_run`          | Run a scene or `scene: "main"` as a background task. Query or cancel by `task`. `capture: true` uses Movie Maker with a separate rendering window; use a suitable display session.                                                                       |

Edits to scene-owned nodes and stored properties are grouped into one undo action when they can be recorded. To delete a node undoably, detach it with `remove_child`, not `free` or `queue_free`. Failed runs attempt to restore the scene and quarantine it if that cannot be proven. Read the scene with `godot_context`, inspect the changes, then pass its revision as `release_quarantine`. This is not a sandbox or a transaction over disk writes, external resources, engine side effects or asynchronous code. Do not use `await` in editor runs.

## Editor dock

Open **OpenCode** and select **Connect**. It creates or resumes the project conversation and displays the agent, model, streamed replies and tool calls. Use **Stop** to interrupt a response and **Disconnect** to release the dock's attachment. You can connect an independent OpenCode conversation to the same editor. Duplicate offer labels are refused by the dock rather than choosing an editor arbitrarily.

The add-on handles discovery and editor requests in GDScript using the local offer protocol. The dock starts a Node client over private pipes, using OpenCode's SDK for sessions and events, following Gaffer's panel. Node is found on `PATH` or via `NEURALNOMADS_GODOT_NODE`. The dock never starts OpenCode. Set `OPENCODE_GODOT_OFFER=0` in the editor environment to disable advertising.

## Tests

Run `node --test integrations/godot/test/*.test.ts` from the repository root. Set `GODOT_TEST_BIN` to a Godot 4.7 editor executable (the console binary on Windows) to also run the real headless tests with private settings and a test service. CI pins Godot 4.7.2.
