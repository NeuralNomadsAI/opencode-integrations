# OpenCode for Microsoft 365

Direct Microsoft Graph tools for SharePoint/OneDrive files, Outlook, Teams, Excel and PowerPoint. Preview integration, MIT licensed. The plugin works in every OpenCode V2 client.

## Install in OpenCode

Add the package to `plugins` in your OpenCode configuration (use the Git source until an npm version is available):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": [
    {
      "package": "github:NeuralNomadsAI/opencode-integrations#main::path:integrations/m365/plugin",
      "options": {
        "clientId": "YOUR_ENTRA_APPLICATION_CLIENT_ID",
        "tenantId": "organizations",
        "scopes": ["offline_access", "openid", "profile", "User.Read"]
      }
    }
  ],
  "permissions": [{ "action": "m365_access", "resource": "*", "effect": "ask" }]
}
```

When published, the package entry is `@neuralnomads/opencode-m365@^0.2`. This repository does not distribute an Entra identity: create your own Microsoft Entra app registration, enable **Allow public client flows**, select the supported account types, and configure delegated Microsoft Graph permissions. Supply its application/client ID as `clientId`. `tenantId` is optional (default `organizations`); set your tenant ID/domain for single-tenant registrations. No client secret is needed for device-code authentication. Tenant policy may require administrator consent or prohibit device-code flow.

Options take precedence over environment fallbacks `NEURALNOMADS_M365_CLIENT_ID`, `NEURALNOMADS_M365_TENANT_ID`, and `NEURALNOMADS_M365_SCOPES` (comma/space separated). The default scopes above permit profile read and refresh only. Configure just the delegated scopes needed for the workflow, then sign in again:

| Workflow             | Typical delegated scopes                                                                                                                               |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Files and PowerPoint | `Files.Read` for reads; `Files.ReadWrite` for writes. Alternatives: `Files.Read.All` / `Files.ReadWrite.All`, `Sites.Read.All` / `Sites.ReadWrite.All` |
| SharePoint discovery | `Sites.Read.All` or `Sites.ReadWrite.All` for site metadata and document-library discovery                                                             |
| Excel workbook APIs  | `Files.ReadWrite` or `Files.ReadWrite.All`, including worksheet and range reads                                                                        |
| Outlook              | `Mail.Read`, `Mail.Send`, `MailboxSettings.ReadWrite`                                                                                                  |
| Teams chats          | `Chat.Read`, `Chat.ReadWrite`                                                                                                                          |
| Teams channels       | `Team.ReadBasic.All`, `Channel.ReadBasic.All`, `ChannelMessage.Read.All`, `ChannelMessage.Send`                                                        |

Call `m365_auth_status`, `m365_auth_device_start`, then open the Microsoft verification URL yourself and enter the returned user code. Call `m365_auth_device_poll` (at most 120 seconds per call), followed by `m365_ping`. `m365_auth_logout` clears the stored credentials and pending login for this app/tenant. Invalid configuration fails with an actionable error; an absent client ID reports `configured: false` and `client_id_required`. Signing out removes local state; revoke consent in Microsoft Entra to revoke previously issued tokens.

Using a `drive_id` avoids site discovery; resolving a site by name or address can additionally require `Sites.Read.All`. Graph enforces access to each target under the granted delegated permissions.

## In CodeNomad

Add the plugin through Plugins, configure your Entra client ID and scopes, and sign in with the tools above. The Bundles catalogue shows this preview integration. Microsoft Graph has no local application host or attachment step.

## Status and safety

RPC ID `neuralnomads.m365` implements contract 1 `status` and empty `status_changed` events. `status.connection.state` is `error` when unconfigured, `signed_out` when configured without a usable login, and `connected` when a local login can be used or refreshed. This is a local credential status, not a live tenant health check. Additional read-only RPC `authStatus` and tool `m365_auth_status` expose `configured`, `signed_in`, granted `scope_list`, configured scopes and expiry metadata, never tokens or device codes.

State is segregated by app/tenant under `$XDG_STATE_HOME/neuralnomads/opencode-m365/` (default `~/.local/state/…`) or `%LOCALAPPDATA%\NeuralNomads\opencode-m365\` on Windows. Token and pending-login files use `0600`, directories `0700`; Windows removes inherited ACLs and grants the current user access. Loading performs no network requests or state writes. Existing credentials from other installations require a fresh sign-in.

Every protected API operation and every authentication mutation asserts the `m365_access` permission, including the expert `m365_graph_request` fallback, which can read, change or delete any resource allowed by the consented scopes. Keep its permission set to `ask`. Mail, bulk changes and notifications additionally offer previews and confirmation arguments. Confirm recipients and targets before sending. Tokens only go to `graph.microsoft.com` and Microsoft's login service; JSON/raw requests reject redirects and arbitrary destinations. Download redirects are restricted to Microsoft's SharePoint/OneDrive HTTPS endpoints and never receive an Authorization header.

Limits: public Microsoft cloud endpoints only; Graph throttling/errors are surfaced rather than retried for mutations; large-file upload sessions and complete pagination are outside the small-file helpers. PowerPoint edits are literal replacements within individual XML text runs; inspect and preview before writing. Device-code flow is unavailable when tenant policy disallows it. Tests use fake credentials and mocked Microsoft responses; tenant-specific consent and end-to-end operation require qualification by the user's administrator.

JSON and raw Graph results recursively remove download/upload capability fields and redact credential-bearing URLs, including nested collections and batch responses. Authorized downloads stream bytes inside the runtime.

## Development

From the repository root: `npm ci`, `npm run build --workspace @neuralnomads/opencode-m365`, `npm run typecheck --workspace @neuralnomads/opencode-m365`, `node --test integrations/m365/test/*.test.ts`, then `npm run check:markers`. All nine `m365-*` skills are loaded by the plugin without replacing user skills of the same name.
