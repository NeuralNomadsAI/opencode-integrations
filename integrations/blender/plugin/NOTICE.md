# Licences of the parts of this package

| Part                                             | Path                                       | Licence                                                                                                           |
| ------------------------------------------------ | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| The plugin, its methods and skills               | `dist/`, `methods/`, `skills/`             | MIT (`LICENSE`)                                                                                                   |
| The OpenCode Link add-on, embedded as an archive | `host/blender/opencode_link-<version>.zip` | GPL-3.0-or-later (`LICENSE` inside the archive); some handlers carry GPL-2.0-or-later headers, compatible with it |
| The Blender documentation data                   | `data/blender_docs/`                       | GPL-3.0-or-later for the packaging, CC-BY-SA-4.0 for the manual text (`data/blender_docs/SOURCE.json`)            |

The `license` field of `package.json` is `MIT AND GPL-3.0-or-later`, as the repository's packaging rules require of a package that embeds a GPL-3.0-or-later application part: the plugin's licence and the add-on's. The data keeps its own licences, stated here and in its folder.
