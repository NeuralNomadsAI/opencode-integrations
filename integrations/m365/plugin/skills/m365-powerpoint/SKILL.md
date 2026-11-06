---

name: m365-powerpoint
description: |
Work with PowerPoint presentations and templates stored in Microsoft 365.

Triggers when user mentions:
- "PowerPoint"
- "presentation"
- "slide"
- "pptx"
- "template powerpoint"

---

## Preferred tools

Use the comfortable tools first:

- `m365_powerpoint_inspect_media`
- `m365_powerpoint_inspect_media_batch`
- `m365_powerpoint_inspect_structure`
- `m365_powerpoint_inspect_text`
- `m365_powerpoint_replace_text`

Only fall back to `m365_graph_request` when a PowerPoint workflow is not covered by the high-level tools.

If authentication is missing, use the `m365-auth` skill first.

## Common flows

### Inspect deck or template text

Call `m365_powerpoint_inspect_text` with a file reference.

Optional:

- `include_template_parts` to include slide layouts and masters
- `max_text_chars` to cap returned previews

### Inspect embedded or linked media

Call `m365_powerpoint_inspect_media` with a PowerPoint file reference.

Optional:

- `include_template_parts` to include references coming from slide layouts and masters
- `include_external_media` to keep or drop linked external media references

### Inspect media across several decks

Call `m365_powerpoint_inspect_media_batch` with:

- shared drive or site reference fields when useful
- `items` as an array of file references

Use this when you need to find which presentations embed images, videos, or linked assets across a small set of files.

### Inspect structure, layouts, and placeholders

Call `m365_powerpoint_inspect_structure` with a PowerPoint file reference.

Use this before template updates when you need to understand:

- slide count
- available layouts and masters
- placeholder counts and types
- core document properties

### Preview text replacements

Call `m365_powerpoint_replace_text` with:

- a PowerPoint file reference
- `replacements`
- optionally `include_template_parts`
- `preview_only: true`

Use this first to confirm which slide or template parts will be changed.

### Apply text replacements

Call `m365_powerpoint_replace_text` again with:

- the same file reference
- the same `replacements`
- `preview_only: false`

Optional:

- `fail_if_missing` when every token must be found
- `max_bytes` for larger decks, within the helper cap

## Guardrails

- Confirm the exact target `.pptx` or `.potx` file before mutating it.
- Use media inspection first when the user asks which deck contains a given image, video, or linked asset.
- Preview replacements before writing when the user is exploring a template.
- The current replacement logic works at the PowerPoint text-run level. Tokens split across multiple formatting runs may need template cleanup first.
- Prefer simple placeholder conventions such as `{{CLIENT_NAME}}` or `{{DATE}}` inside templates.
