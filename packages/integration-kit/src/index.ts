// Public entrypoint of the integration kit, bundled into each integration plugin.

export type { CoherenceCheckOptions, CoherenceReport } from "./coherence-check.ts"
export { printCoherenceReport, runCoherenceCheck } from "./coherence-check.ts"
export type {
  AdvertiseInput,
  AdvertiseOutcome,
  AttachOutcome,
  ConnectionState,
  ConnectionStatus,
  HostOffer,
  HostState,
  HostStatus,
  InstallHostOutput,
  Notice,
  StatusOutput,
  WithdrawOutcome,
} from "./contract.ts"
export {
  boundedDetail,
  CONTRACT_VERSION,
  DETAIL_MAX,
  displayLabel,
  HELLO_TIMEOUT_MS,
  KNOWN_NOTICES,
  LABEL_MAX,
  LIVE_HELLO_MS,
  MAX_OFFERS,
  NATIVE_METHODS,
  OFFER_LEASE_MS,
  OFFER_RENEW_MS,
  parseAdvertiseInput,
  parseWithdrawInput,
  RPC_SCHEMAS,
  rpcDefinition,
  SPEC_DRAFT,
} from "./contract.ts"
export type { Hello, HostCapabilities, HostCompatibility, HostExpectation, UndoLevel } from "./hello.ts"
export {
  appVersionSatisfies,
  canonicalJson,
  capabilityHash,
  comparePartVersion,
  evaluateHello,
  parseHello,
} from "./hello.ts"
export type { HostEndpoint } from "./host-client.ts"
export { HOST_ADDRESS, HostError, requestHost } from "./host-client.ts"
export type { HostInspection, HostStateInput, LiveHello, WslProbe } from "./host-state.ts"
export { deriveHostState, hostUnsupported, isWslDaemon } from "./host-state.ts"
export type { IntegrationRpcOptions, RpcEventSink } from "./integration-rpc.ts"
export { createIntegrationRpc } from "./integration-rpc.ts"
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
export type { HelloProbe, HostBinding, OfferChange, OfferRegistry, OfferRegistryOptions } from "./offer-registry.ts"
export { createOfferRegistry, MAX_SESSIONS, offerRegistryKey, sharedOfferRegistry } from "./offer-registry.ts"
export { errorToMessage, toJson } from "./output.ts"
export type { ManifestExecutor, ToolContent } from "./plugin-tools.ts"
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
