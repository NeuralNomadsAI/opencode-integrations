const string = { type: "string" } as const
const strings = { type: "array", items: string, maxItems: 50 } as const
export const contextInput = {
  type: "object",
  additionalProperties: false,
  required: ["script_handle"],
  properties: {
    script_handle: string,
    if_revision: { type: "integer", minimum: 0 },
    refs: strings,
    match: { type: "object", properties: { name: string, type: string }, additionalProperties: false },
    radius: { type: "integer", minimum: 0, maximum: 2 },
    plugs: strings,
    plug_limit: { type: "integer", minimum: 1, maximum: 1000 },
    recipe_match: {
      type: "object",
      properties: { id: string, node_type: string, menu_path: string, variant: string },
      additionalProperties: false,
    },
    job: {
      type: "object",
      properties: {
        id: string,
        after: { type: "integer", minimum: 0 },
        limit: { type: "integer", minimum: 1, maximum: 200 },
      },
      required: ["id"],
      additionalProperties: false,
    },
    include: {
      type: "array",
      items: { enum: ["values", "connections", "schema", "affordances", "capabilities", "recipes", "renders"] },
      uniqueItems: true,
    },
    limit: { type: "integer", minimum: 1, maximum: 100 },
  },
} as const
export const applyInput = {
  type: "object",
  additionalProperties: false,
  required: ["script_handle", "if_revision", "command_id", "operations"],
  properties: {
    script_handle: string,
    if_revision: { type: "integer", minimum: 0 },
    command_id: string,
    summary: string,
    operations: {
      type: "array",
      minItems: 1,
      maxItems: 50,
      items: {
        type: "object",
        required: ["op"],
        additionalProperties: false,
        properties: {
          op: {
            enum: [
              "node.create",
              "node.rename",
              "plug.set_value",
              "graph.connect",
              "graph.connect_many",
              "graph.delete",
              "output.define",
              "render.start",
              "render.cancel",
              "ui.view",
            ],
          },
          target: string,
          source: string,
          destination: string,
          sources: { type: "array", items: string, minItems: 1, maxItems: 100 },
          start_index: { type: "integer", minimum: 0 },
          value: {},
          type: string,
          recipe: string,
          variant: string,
          name: string,
          parent: string,
          bind: { type: "string", pattern: "^[A-Za-z_][A-Za-z0-9_]*$" },
          renderer: string,
          file_name: string,
          driver: string,
          channels: string,
          frame_range: string,
          parameters: {
            type: "object",
            additionalProperties: { anyOf: [string, { type: "number" }, { type: "boolean" }] },
          },
        },
      },
    },
    expect: {
      type: "array",
      maxItems: 50,
      items: {
        oneOf: [
          {
            type: "object",
            properties: { subject: string, predicate: { const: "exists" }, value: { type: "boolean" } },
            required: ["subject", "predicate"],
            additionalProperties: false,
          },
          {
            type: "object",
            properties: { subject: string, predicate: { const: "name_equals" }, value: string },
            required: ["subject", "predicate", "value"],
            additionalProperties: false,
          },
          {
            type: "object",
            properties: { subject: string, predicate: { const: "value_equals" }, value: {} },
            required: ["subject", "predicate", "value"],
            additionalProperties: false,
          },
          {
            type: "object",
            properties: {
              subject: string,
              predicate: { const: "connected_to" },
              value: { anyOf: [string, { type: "null" }] },
            },
            required: ["subject", "predicate", "value"],
            additionalProperties: false,
          },
        ],
      },
    },
  },
} as const
