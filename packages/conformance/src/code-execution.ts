// The only accepted forms of a method that executes supplied code
// (spec/integrations.md, rule SEC-3): declared `dangerous` and gated on every
// call by the permission `<app>_execute_code`; either absent unless both the
// application preference and the plugin option `allowCodeExecution` are true
// (a driver that declares `defaults`), or listed while the application part is
// enabled (a driver without `defaults`).

export type ManifestLike = {
  name: string
  risk?: string
  dangerous?: boolean
  execution?: { method?: string; tool?: string }
}

export type CodeExecutionSettings = { appPreference: boolean; pluginOption: boolean }

export type CodeExecutionCall = { executed: boolean; permissionsAsked: string[]; error?: string }

export type CodeExecutionProbe = {
  // Host methods listed by hello.capabilities.methods.
  helloMethods: string[]
  // Tool IDs the plugin registers.
  toolNames: string[]
  // Calls the dangerous tool once; `permission` is the answer to any permission request.
  call(permission: "allow" | "deny"): Promise<CodeExecutionCall>
}

// The settings gate (SEC-3 2a): the defaults of both settings, each combination probed.
export type GatedCodeExecutionDriver = {
  app: string
  manifests: ManifestLike[]
  defaults: CodeExecutionSettings
  probe(settings: CodeExecutionSettings): Promise<CodeExecutionProbe>
}

// A method listed while the application part is enabled (SEC-3 2b): no settings, probed once.
export type PresentCodeExecutionDriver = {
  app: string
  manifests: ManifestLike[]
  defaults?: undefined
  probe(): Promise<CodeExecutionProbe>
}

export type CodeExecutionDriver = GatedCodeExecutionDriver | PresentCodeExecutionDriver

// Names that suggest code execution; such a method must be the declared one.
export const CODE_EXECUTION_NAME =
  /(^|[._])(execute_code|exec|eval|run_code|run_python|python_exec|exec_python)($|[._])/

export function permissionFor(app: string) {
  return `${app}_execute_code`
}

function hostMethodOf(manifest: ManifestLike) {
  return manifest.execution?.method ?? manifest.name.replace(/^[a-z][a-z0-9-]*_/, "")
}

// Static rules on the method manifests of a package.
export function checkDangerousManifests(app: string, manifests: ManifestLike[]): string[] {
  const errors: string[] = []
  const dangerous = manifests.filter((manifest) => manifest.dangerous === true)
  if (dangerous.length > 1) errors.push(`methods: at most one dangerous method, found ${dangerous.length}`)
  for (const manifest of manifests) {
    const executes =
      CODE_EXECUTION_NAME.test(manifest.name) || CODE_EXECUTION_NAME.test(manifest.execution?.method ?? "")
    if (executes && manifest.dangerous !== true) {
      errors.push(`methods: ${manifest.name} executes supplied code but is not declared dangerous`)
    }
  }
  for (const manifest of dangerous) {
    if (manifest.name !== permissionFor(app))
      errors.push(`methods: the dangerous method must be named ${permissionFor(app)}`)
    if (manifest.risk !== "destructive") errors.push(`methods: ${manifest.name} must have risk "destructive"`)
  }
  return errors
}

const COMBINATIONS: CodeExecutionSettings[] = [
  { appPreference: false, pluginOption: false },
  { appPreference: true, pluginOption: false },
  { appPreference: false, pluginOption: true },
  { appPreference: true, pluginOption: true },
]

function describe(settings: CodeExecutionSettings) {
  return `application preference ${settings.appPreference}, plugin option ${settings.pluginOption}`
}

// A permission-gated call: denied, nothing runs; allowed, it runs; both ask the permission.
async function checkPermission(probe: CodeExecutionProbe, permission: string, errors: string[]) {
  const denied = await probe.call("deny")
  if (!denied.permissionsAsked.includes(permission)) errors.push(`code execution: a call does not ask ${permission}`)
  if (denied.executed) errors.push("code execution: code runs although the permission was denied")
  const allowed = await probe.call("allow")
  if (!allowed.permissionsAsked.includes(permission)) {
    errors.push(`code execution: code runs without the permission ${permission}`)
  }
  if (!allowed.executed) errors.push("code execution: an allowed call does not run")
}

export async function checkCodeExecutionGate(driver: CodeExecutionDriver): Promise<string[]> {
  const errors = checkDangerousManifests(driver.app, driver.manifests)
  const defaults = driver.defaults
  if (defaults && defaults.appPreference !== false)
    errors.push("code execution: the application preference defaults to true")
  if (defaults && defaults.pluginOption !== false) errors.push("code execution: allowCodeExecution defaults to true")

  const dangerous = driver.manifests.find((manifest) => manifest.dangerous === true)
  if (!dangerous) {
    const probe = driver.defaults ? await driver.probe(driver.defaults) : await driver.probe()
    for (const method of probe.helloMethods) {
      if (CODE_EXECUTION_NAME.test(method))
        errors.push(`code execution: hello lists ${method} without a declared method`)
    }
    return errors
  }

  const hostMethod = hostMethodOf(dangerous)
  const tool = dangerous.execution?.tool ?? dangerous.name
  const permission = permissionFor(driver.app)
  if (!driver.defaults) {
    // Listed while the application part is enabled: present, and asked on every call.
    const probe = await driver.probe()
    if (!probe.helloMethods.includes(hostMethod)) errors.push(`code execution: hello omits ${hostMethod}`)
    if (!probe.toolNames.includes(tool)) errors.push(`code execution: the plugin omits ${tool}`)
    await checkPermission(probe, permission, errors)
    return [...new Set(errors)]
  }
  const gated = driver
  for (const settings of [gated.defaults, ...COMBINATIONS]) {
    const enabled = settings.appPreference && settings.pluginOption
    const probe = await gated.probe(settings)
    const label = describe(settings)
    if (probe.helloMethods.includes(hostMethod) !== enabled) {
      errors.push(`code execution: hello ${enabled ? "omits" : "lists"} ${hostMethod} with ${label}`)
    }
    if (probe.toolNames.includes(tool) !== enabled) {
      errors.push(`code execution: the plugin ${enabled ? "omits" : "exposes"} ${tool} with ${label}`)
    }
    if (!enabled) {
      const call = await probe.call("allow")
      if (call.executed) errors.push(`code execution: code runs with ${label}`)
      if (call.error !== "method_disabled")
        errors.push(`code execution: a call is not refused as method_disabled with ${label}`)
      continue
    }
    await checkPermission(probe, permission, errors)
  }
  return [...new Set(errors)]
}
