// project.godot keeps every line it had when OpenCode Link is enabled in it.

import assert from "node:assert/strict"
import { it } from "node:test"

import { enabledIn, enablePlugin, PLUGIN_CFG } from "../plugin/src/host-install.ts"

const ENTRY = `"${PLUGIN_CFG}"`

it("adds the [editor_plugins] section when the project has none", () => {
  const text = 'config_version=5\n\n[application]\n\nconfig/name="Game"\n'
  const enabled = enablePlugin(text)
  assert.equal(enabled, `${text}\n[editor_plugins]\n\nenabled=PackedStringArray(${ENTRY})\n`)
  assert.deepEqual(enabledIn(enabled), [PLUGIN_CFG])
})

it("adds the add-on next to the project's other editor plugins, in their section", () => {
  const text =
    '[application]\n\nconfig/name="Game"\n\n[editor_plugins]\n\nenabled=PackedStringArray("res://addons/other/plugin.cfg")\n\n[rendering]\n\nx=1\n'
  const enabled = enablePlugin(text)
  assert.equal(
    enabled,
    `[application]\n\nconfig/name="Game"\n\n[editor_plugins]\n\nenabled=PackedStringArray("res://addons/other/plugin.cfg", ${ENTRY})\n\n[rendering]\n\nx=1\n`,
  )
  assert.deepEqual(enabledIn(enabled), ["res://addons/other/plugin.cfg", PLUGIN_CFG])
})

it("fills an empty list and a section without one, keeping CRLF line ends", () => {
  assert.deepEqual(enabledIn(enablePlugin("[editor_plugins]\n\nenabled=PackedStringArray()\n")), [PLUGIN_CFG])
  const crlf = '[editor_plugins]\r\n\r\n[gui]\r\n\r\ntheme="x"\r\n'
  const enabled = enablePlugin(crlf)
  assert.equal(enabled, `[editor_plugins]\r\n\r\nenabled=PackedStringArray(${ENTRY})\r\n[gui]\r\n\r\ntheme="x"\r\n`)
  assert.deepEqual(enabledIn(enabled), [PLUGIN_CFG])
  // The list of another section is not this one's.
  assert.deepEqual(enabledIn('[other]\nenabled=PackedStringArray("res://x")\n'), [])
})
