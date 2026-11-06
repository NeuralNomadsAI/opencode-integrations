# OpenCode for DaVinci Resolve

Preview, spec draft 0.2. An independent MIT OpenCode V2 plugin wrapping the
vendor MCP supplied with **DaVinci Resolve Studio 21.1+**. DaVinci Resolve is a
Blackmagic Design trademark, used nominatively. This project is unaffiliated
with Blackmagic Design or OpenCode. The vendor server remains separately installed
and licensed by its vendor; this package contains skills and an offline check.

## Install in any OpenCode client

Add `@neuralnomads/opencode-resolve@^0.2` to `plugins`. Until publication, use a
built local package directory instead. With the object form, supply `options`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": [
    {
      "package": "@neuralnomads/opencode-resolve@^0.2",
      "options": {
        "studio": true,
        "appVersion": "21.1",
        "command": ["/absolute/path/from-your-installed-vendor-configuration"]
      }
    }
  ]
}
```

Obtain the actual command and arguments from your installed product's
**File → Setup AI Assistants** configuration. Verify it belongs to your trusted
vendor installation. The example path is a placeholder, not a known executable.
On Windows use an absolute Windows path (escaped backslashes or forward slashes).
The `command` array is passed directly, without a shell.
`studio` and `appVersion` are operator attestations, not automatic licence/version detection.
The offline check verifies the edition/version attestations and executable file
existence; it never launches or authenticates the product.

Options override environment fallbacks: `NEURALNOMADS_RESOLVE_STUDIO` (`true`),
`NEURALNOMADS_RESOLVE_APP_VERSION`, and `NEURALNOMADS_RESOLVE_COMMAND` (JSON array).
Malformed options fail closed. Without satisfied checks, no server is added.
When checks pass, the plugin declares server ID `resolve` as local stdio and
**disabled**. Inspect the declaration and explicitly connect in `/mcps`.
An existing `resolve` entry is always preserved, including its transport and flags.
Existing user entries remain usable even without the attestations above.

The `neuralnomads.resolve` RPC implements `status` and `status_changed`.
Status reads current OpenCode MCP connection state (`connected`, `none`, `error`),
with no native host fields, paths, credentials or raw error messages.
It reports MCP connectivity, not product readiness or a verified project.
`mcp.status.changed` invalidates status. Load the `resolve-workflows` skill for project,
Fusion and render workflows. User skills with that ID are preserved.

## Permissions and safety

Every vendor tool under `resolve_*`, including tools added by catalog refresh,
is wrapped with the named **`resolve_execute_code`** permission (ask by default).
A denial propagates and never executes the vendor tool. Existing explicit user
permission rules still apply. A vendor tool may run arbitrary Python/Lua with
disk and network access. Approve only code and project changes you understand;
back up projects first. No atomic undo guarantee is made.
Connecting the vendor MCP may launch Resolve; make that choice explicitly.
Plugin loading performs no launch, network access, download or installation.

## In CodeNomad

Use the existing Plugins surface to add the built package. The integrations
catalog lists this package as preview. Check connection in the MCP surface;
this vendor-managed family has no installable host or conversation attachment.

## Read-only script

After building, from this package directory:

```sh
node scripts/check-preconditions.mjs '{"studio":true,"appVersion":"21.1","command":["/absolute/vendor/executable"]}'
```

Outputs `{ "ready": boolean, "issues": [...] }`; exits 1 when prerequisites are
missing. It outputs no paths or command text and changes no configuration.

## Evidence and qualification limits

The [official 21.1 Reference Manual, p. 4318](https://documents.blackmagicdesign.com/UserManuals/DaVinci_Resolve_21.1_Reference_Manual.pdf#page=4318)
documents the built-in Studio MCP, File → Setup AI Assistants, potential automatic
application launch and scripts sharing the assistant's disk/network permissions.
It supplies **no executable command, arguments, transport or tool inventory**.
`ResolveMCP` is the task/catalog designation, not a verified executable basename.

This preview assumes the operator's vendor command supports **local stdio**.
No default command, path, port or tool count is invented. If the installed vendor
configuration uses another transport, provide the corresponding user-owned
`resolve` MCP entry; the plugin preserves it. Remote authentication must be
configured through OpenCode's supported credentials mechanism.
The exact tool names, schemas and count must be obtained from the installed
server's MCP catalog before using the workflows. No server or manual is redistributed.

No Resolve Studio installation was found in the standard Windows installation
locations or uninstall registry on the development machine. Custom locations
remain possible. Tests use the native plugin interfaces with an in-memory MCP
catalog; live vendor handshake, tool count, authentication and Resolve/Fusion
operation remain unqualified. The prior external-scripting source informed the
workflow scope only; its bridge and tool names are not used by this MCP port.
