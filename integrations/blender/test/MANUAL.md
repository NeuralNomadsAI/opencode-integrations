# Interface protocol (AC-B3 interface part, AC-B4, AC-B6 interface part)

The CI runs the host tests in a background Blender. Undo history, Edit mode and captures with the interface are checked with this protocol, on a workstation, before each release of OpenCode Link. Record the printed JSON result in the pull request.

## Requirements

- Blender 5.2 or later, its executable in `BLENDER_TEST_BIN`.
- This repository with `npm ci` done. No OpenCode service is needed: the driver stands in for one.
- Nothing touches your own Blender profile: the driver starts Blender in a temporary profile (user resources, HOME, XDG and temporary folders), installs OpenCode Link there from the sources, and enables it.

## Guided run

```sh
node integrations/blender/test/manual/drive.ts
```

Blender opens without taking the focus. Answer each question in the terminal.

| #   | You do                                                                                  | You check                                                                                                                                      | Criterion      |
| --- | --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | -------------- |
| 1   | In the 3D Viewport sidebar (N) ▸ **OpenCode**, tick **Offer this session to OpenCode**. | The terminal prints `PASS consent`. Nothing was offered before the tick.                                                                       | HST-10         |
| 2   | — (the driver applies three cube creations in one apply)                                | **Edit ▸ Undo History** shows **one** step `OpenCode: three cubes`. One **Ctrl+Z** removes the three cubes; **Ctrl+Shift+Z** brings them back. | AC-B4          |
| 3   | — (the driver applies a creation followed by a failing deletion)                        | No `Manual_Partial` object exists; the steps before it are intact in the Undo History.                                                         | AC-B4, model B |
| 4   | Select `Manual_A`, press **Tab** (Edit mode).                                           | The terminal prints `PASS … mode_conflict`. Press **Tab** again.                                                                               | AC-B3          |
| 5   | Move `Manual_A` (**G**, move, click).                                                   | `PASS … stale_revision`, then `PASS replaying a command_id applies nothing`.                                                                   | AC-B3          |
| 6   | — (the driver captures the viewport)                                                    | `PASS viewport capture returns a PNG`.                                                                                                         | AC-B6          |
| 7   | Untick **Offer this session to OpenCode**.                                              | `PASS unticking withdraws the offer` and `PASS the listener is closed`.                                                                        | HST-10, AC-B15 |

The run ends with a JSON summary; every step must be `"ok": true`.

## Unattended run

```sh
node integrations/blender/test/manual/drive.ts --auto
```

The same steps without questions: a startup script ticks the consent, and the driver performs the undo, redo, Edit mode and object move itself through the dangerous method, which it enables in the temporary profile only (`Allow code execution`, and the plugin-side flag of its requests). It checks the undo effects by reading the object list. It does not replace looking at the Undo History in step 2 once per release.

## Quarantine

A failed restoration cannot be provoked from the interface: a modal operator only makes the add-on answer `busy`. The background host test `a_failed_restoration_quarantines_the_host` covers it by making the undo fail. In the interface, the quarantine shows as "Quarantined" in the sidebar, and **Reset OpenCode Link** clears it.

## Last recorded result

Blender 5.2.0 LTS Beta (Windows), unattended run: 13 of 13 steps passed.
