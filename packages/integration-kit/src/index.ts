// Public entrypoint of the integration kit, bundled into each integration plugin.

export type { CoherenceCheckOptions, CoherenceReport } from "./coherence-check.ts"
export { printCoherenceReport, runCoherenceCheck } from "./coherence-check.ts"
export { validateAndNormalizeArgs } from "./manifest-args.ts"
export type { HostMarker, IntegrationFamily, IntegrationMarker, VendorMarker } from "./marker.ts"
export {
  integrationIdFromPackageName,
  PACKAGE_PREFIX,
  PACKAGE_SCOPE,
  PLUGIN_ID_PREFIX,
  pluginIdFor,
  SPEC_VERSION,
} from "./marker.ts"
export type { MethodRegistry } from "./method-registry.ts"
export { loadMethodRegistry } from "./method-registry.ts"
export { errorToMessage, toJson } from "./output.ts"
export type { ManifestExecutor } from "./plugin-tools.ts"
export { buildPluginTools, buildToolInput, registerPluginTools } from "./plugin-tools.ts"
export type {
  JsonObject,
  MethodArgManifest,
  MethodArgType,
  MethodExecution,
  MethodKind,
  MethodManifest,
  MethodRequires,
  MethodRisk,
  MethodVerify,
} from "./types.ts"
export { ensureJsonObject, isRecord } from "./validators.ts"
