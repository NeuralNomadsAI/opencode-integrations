# Security

## Reporting a vulnerability

Report vulnerabilities privately through **GitHub Security Advisories** ("Report a vulnerability" on the repository's Security tab). Do not open a public issue.

## Rules every integration follows

The specification (`spec/`) is normative; in short:

1. **Authenticated hosts.** An application host listens on `127.0.0.1` only, on an ephemeral port, and requires a random 256-bit token per process, compared in constant time. The token lives in memory only: never on disk, in logs or in any client response. The plugin refuses any non-loopback address.
2. **No public arbitrary code execution.** Hosts expose a fixed table of methods; the plugin sends `{ method, args }` JSON only and never imports code from the npm cache. The only exception is one declared `dangerous` method per `native` integration, gated by an OpenCode permission ("ask" unless the user allows it) on every call; it is either absent by default (both the application preference and the plugin option `allowCodeExecution` default to `false`) or listed while the application part is enabled. A plugin may run its own fixed programs through that method, read only, with their parameters as data.
3. **Secrets outside the watched OpenCode configuration.** No token, password or OAuth secret under `~/.config/opencode`: the OpenCode credential store first, then the OS keychain, then the integration's own state directory with `0600` permissions.
4. **No hard-coded organization identifiers**: no Entra client or tenant ID, studio path or network share.
5. **Named permissions** for sensitive actions: launching executables, installing the application part, attaching a session, network access from a host method.
6. **Supply chain.** npm publication through GitHub Actions trusted publishing (OIDC) with provenance, after a manual approval; attested host archives; no npm install scripts.

## Dangerous methods

| Integration          | Method                                                           | Enabled when                        | Asked through                                                                |
| -------------------- | ---------------------------------------------------------------- | ----------------------------------- | ---------------------------------------------------------------------------- |
| OpenCode for Blender | `blender_execute_code`: runs Python code in the attached Blender | the OpenCode Link add-on is enabled | the `blender_execute_code` permission, on every call; a refusal runs nothing |

Supplied code runs with Blender's rights: it can read and write files, start processes and reach the network. OpenCode Link makes a run that changes the scene one undo step and undoes a failing one; effects outside the scene are not undone. The plugin's `blender_inspect`, `blender_verify`, `blender_render` and `blender_save` run fixed programs of the package through the same method, read only, without asking: they execute no code from the model.

## Residual risks

- **Daemon password read by an application add-on.** A headless `native` add-on (Blender) reads the OpenCode service registration, password included, with the user's rights, to advertise its session. That grants the add-on code full access to the OpenCode service of the same user.
- **Fake local host.** A local process of the same user can advertise a host that passes its own handshake and inject responses into the model's context. Mitigations: consent in the application, a labelled confirmation (application, document) before an explicit attachment, loopback only. OpenCode for Blender attaches the only offered Blender automatically, without confirmation, when no conversation uses it: a fake host that is the only offer is attached the same way. Such a process already runs with the user's rights.
