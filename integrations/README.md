# Integrations

Each integration lives in `integrations/<app>/`:

| Path      | Content                                                                                                                    |
| --------- | -------------------------------------------------------------------------------------------------------------------------- |
| `plugin/` | The npm package `@neuralnomads/opencode-<app>`: compiled plugin, method manifests, skills, data, embedded application part |
| `host/`   | Sources of the application part, for `native` integrations                                                                 |
| `test/`   | Host and end-to-end tests                                                                                                  |

Blender is the pilot. See [CONTRIBUTING.md](../CONTRIBUTING.md) for the conventions every integration follows.
