// Method manifest types shared by every integration of this repository.
// Manifests under <plugin>/methods/**.json are the source of truth for tools.

export type JsonObject = Record<string, unknown>

export type MethodArgType = "string" | "integer" | "number" | "boolean" | "object" | "array" | "any"

export type MethodArgManifest = {
  type: MethodArgType
  required?: boolean
  description?: string
  default?: unknown
  enum?: string[]
  items?: MethodArgManifest
}

export type MethodRisk = "read" | "write" | "destructive"

export type MethodKind = "host-backed" | "hostless" | (string & {})

// Execution strategy of a method. Integrations add their own variants through
// the `strategy` discriminator (for example a fixed host method name, or an
// in-process handler keyed by manifest.name).
export type MethodExecution = {
  strategy: string
  // Tool that exposes the method. Defaults to manifest.name; a public method
  // must set tool === name.
  tool?: string
  // Fixed host method name, for host-backed strategies.
  method?: string
  [key: string]: unknown
}

export type MethodRequires = {
  // Host methods the method depends on.
  hostMethods?: string[]
  // API requirements of hostless methods.
  auth?: boolean
  scopes?: string[]
  env?: string[]
  [key: string]: unknown
}

export type MethodVerify = {
  strategy?: string
  method?: string
  [key: string]: unknown
}

export type MethodManifest = {
  name: string
  domain: string
  description: string
  kind: MethodKind
  args?: Record<string, MethodArgManifest>
  returns?: Record<string, unknown>
  requires?: MethodRequires
  execution: MethodExecution
  verify?: MethodVerify
  risk: MethodRisk
  // Executes caller-supplied code: allowed only under the rules of the
  // `dangerous` method in the specification (disabled by default, "ask").
  dangerous?: boolean
}
