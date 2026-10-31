# Security

## Reporting a vulnerability

Report vulnerabilities privately through **GitHub Security Advisories** ("Report a vulnerability" on the repository's Security tab). Do not open a public issue.

## Rules every integration follows

The specification (`spec/`) is normative; in short:

1. **Authenticated hosts.** An application host listens on `127.0.0.1` only, on an ephemeral port, and requires a random 256-bit token per process, compared in constant time. The token lives in memory only: never on disk, in logs or in any client response. The plugin refuses any non-loopback address.
2. **No public arbitrary code execution.** Hosts expose a fixed table of methods; the plugin sends `{ method, args }` JSON only and never imports code from the npm cache. The only exception is one declared `dangerous` method per `native` integration, absent by default (both the application preference and the plugin option `allowCodeExecution` default to `false`) and gated by an OpenCode permission set to "ask" on every call.
3. **Secrets outside the watched OpenCode configuration.** No token, password or OAuth secret under `~/.config/opencode`: the OpenCode credential store first, then the OS keychain, then the integration's own state directory with `0600` permissions.
4. **No hard-coded organization identifiers**: no Entra client or tenant ID, studio path or network share.
5. **Named permissions** for sensitive actions: launching executables, installing the application part, attaching a session, network access from a host method.
6. **Supply chain.** npm publication through GitHub Actions trusted publishing (OIDC) with provenance, after a manual approval; attested host archives; no npm install scripts.

## Residual risks

- **Daemon password read by an application add-on.** A headless `native` add-on (Blender) reads the OpenCode service registration, password included, with the user's rights, to advertise its session. That grants the add-on code full access to the OpenCode service of the same user.
- **Fake local host.** A local process of the same user can advertise a host that passes its own handshake and inject responses into the model's context. Mitigations: consent in the application, a labelled confirmation (application, document) before any attachment, loopback only.
