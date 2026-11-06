---

name: m365-auth
description: Sign in to Microsoft Graph with a user-owned Entra application.
---

1. Read `m365_auth_status`. If `configured` is false, the user must supply their own Entra client ID in the plugin options or `NEURALNOMADS_M365_CLIENT_ID`.
2. Confirm the user's intent to sign in, then call `m365_auth_device_start`.
3. Tell the user to open the returned Microsoft verification URL and enter `user_code`. Never request a password in chat.
4. Call `m365_auth_device_poll` until sign-in succeeds, then `m365_ping`.
5. Use `m365_auth_logout` to remove the local login.

The user configures `tenantId` and delegated `scopes` in plugin options. Defaults are `organizations` and profile-only scopes. Additional Graph capabilities need explicit configuration and consent followed by sign-in. Tool arguments cannot override the configured app, tenant or scopes. Tokens and the device code stay in the private per-user integration state directory; never read or copy them into the conversation.
