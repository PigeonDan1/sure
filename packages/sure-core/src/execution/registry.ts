import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type {
	ExecutionOperation,
	ExecutorIdentity,
	ExecutorKind,
	ExecutorTrustLevel,
	JsonValue,
} from "../contracts/types.ts";
import { type ExternalAdapterHostBinding, validateExternalAdapterRegistration } from "./adapter-policy.ts";
import type { ExecutorPort } from "./types.ts";

export type ExecutorImplementation = "builtin" | "external_registration_required";

/**
 * Stable description of an executor adapter.  A descriptor is an identity and
 * admission record, not a capability claim: the selected adapter still has to
 * emit authoritative probe evidence in its execution receipt.
 */
export interface ExecutorDescriptor {
	executor_id: string;
	kind: ExecutorKind;
	minimum_trust_level: ExecutorTrustLevel;
	implementation: ExecutorImplementation;
	capability_ids: readonly string[];
	operations: readonly ExecutionOperation[];
}

export interface ExecutorRegistrySnapshot {
	schema: "sure.executor.registry.v1";
	registry_digest: string;
	executors: readonly ExecutorDescriptor[];
}

const ALL_OPERATIONS: readonly ExecutionOperation[] = [
	"validation",
	"inference",
	"formal_evaluation",
	"package",
	"publication",
];

const EXECUTOR_DESCRIPTORS: readonly ExecutorDescriptor[] = [
	{
		executor_id: "surectl.local",
		kind: "local",
		minimum_trust_level: "cooperative",
		implementation: "builtin",
		capability_ids: ["sure.execution.local-python", "sure.execution.model-runtime"],
		operations: ALL_OPERATIONS,
	},
	{
		executor_id: "surectl.python",
		kind: "python",
		minimum_trust_level: "cooperative",
		implementation: "builtin",
		capability_ids: [
			"sure.execution.evaluation-runtime",
			"sure.execution.harness-python",
			"sure.execution.local-python",
			"sure.execution.model-runtime",
			"sure.execution.source-runtime",
		],
		operations: ALL_OPERATIONS,
	},
	{
		executor_id: "surectl.docker",
		kind: "docker",
		minimum_trust_level: "cooperative",
		implementation: "builtin",
		capability_ids: ["sure.execution.docker", "sure.execution.docker-optional", "sure.execution.source-runtime"],
		operations: ALL_OPERATIONS,
	},
	{
		executor_id: "sure.external.remote",
		kind: "remote",
		minimum_trust_level: "cooperative",
		implementation: "external_registration_required",
		capability_ids: ["sure.execution.remote"],
		operations: ALL_OPERATIONS,
	},
	{
		executor_id: "sure.external.trusted",
		kind: "trusted",
		minimum_trust_level: "attested",
		implementation: "external_registration_required",
		capability_ids: ["sure.execution.trusted"],
		operations: ALL_OPERATIONS,
	},
];

function cloneDescriptor(descriptor: ExecutorDescriptor): ExecutorDescriptor {
	return {
		...descriptor,
		capability_ids: [...descriptor.capability_ids],
		operations: [...descriptor.operations],
	};
}

export function executorDescriptors(): readonly ExecutorDescriptor[] {
	return EXECUTOR_DESCRIPTORS.map(cloneDescriptor);
}

export function executorDescriptor(kind: ExecutorKind): ExecutorDescriptor | undefined {
	const descriptor = EXECUTOR_DESCRIPTORS.find((entry) => entry.kind === kind);
	return descriptor === undefined ? undefined : cloneDescriptor(descriptor);
}

export function executorRegistrySnapshot(): ExecutorRegistrySnapshot {
	const unsigned = {
		schema: "sure.executor.registry.v1" as const,
		executors: executorDescriptors(),
	};
	return {
		...unsigned,
		registry_digest: canonicalJsonDigest(unsigned as unknown as JsonValue),
	};
}

const TRUST_RANK: Readonly<Record<ExecutorTrustLevel, number>> = {
	cooperative: 0,
	host_enforced: 1,
	attested: 2,
};

export interface ExternalExecutorRegistration {
	readonly port: ExecutorPort;
	/** Executor identity captured at registration; the live port cannot retarget it. */
	readonly identity: ExecutorIdentity;
	readonly binding: ExternalAdapterHostBinding;
}

function normalizeDigest(value: string): string {
	return value.replace(/^sha256:/i, "").toLowerCase();
}

/** Runtime registry for deployment-provided executor ports. */
export class ExecutorRegistry {
	private readonly descriptors = new Map<ExecutorKind, ExecutorDescriptor>();
	private readonly ports = new Map<ExecutorKind, ExecutorPort>();
	private readonly externalPorts = new Map<string, ExternalExecutorRegistration>();

	constructor(descriptors: readonly ExecutorDescriptor[] = executorDescriptors()) {
		for (const descriptor of descriptors) {
			if (this.descriptors.has(descriptor.kind))
				throw new Error(`Duplicate executor descriptor kind: ${descriptor.kind}`);
			this.descriptors.set(descriptor.kind, descriptor);
		}
	}

	descriptor(kind: ExecutorKind): ExecutorDescriptor | undefined {
		return this.descriptors.get(kind);
	}

	private validatePort(port: ExecutorPort): ExecutorDescriptor {
		const descriptor = this.descriptors.get(port.identity.kind);
		if (!descriptor) throw new Error(`Executor kind ${port.identity.kind} is not declared by the registry`);
		if (!port.identity.executor_id.trim() || !port.identity.version.trim())
			throw new Error("Executor identity requires non-empty executor_id and version");
		if (!/^(?:sha256:)?[0-9a-f]{64}$/i.test(port.identity.digest))
			throw new Error("Executor identity digest must be SHA-256");
		if (TRUST_RANK[port.identity.trust_level] < TRUST_RANK[descriptor.minimum_trust_level]) {
			throw new Error(
				`Executor ${port.identity.executor_id} does not meet ${descriptor.minimum_trust_level} trust for ${descriptor.kind}`,
			);
		}
		return descriptor;
	}

	register(port: ExecutorPort): void {
		const descriptor = this.validatePort(port);
		if (descriptor.implementation === "external_registration_required") {
			throw new Error(`Executor kind ${descriptor.kind} must be registered with registerExternal`);
		}
		if (this.ports.has(port.identity.kind))
			throw new Error(`Executor kind ${port.identity.kind} is already registered`);
		this.ports.set(port.identity.kind, port);
	}

	registerExternal(port: ExecutorPort, binding: ExternalAdapterHostBinding): void {
		const descriptor = this.validatePort(port);
		if (descriptor.implementation !== "external_registration_required") {
			throw new Error(`Executor kind ${descriptor.kind} does not accept external adapter registration`);
		}
		const validation = validateExternalAdapterRegistration(port.identity, binding);
		if (!validation.valid || validation.manifest === undefined) {
			throw new Error(`External adapter registration rejected: ${validation.errors.join("; ")}`);
		}
		const key = normalizeDigest(validation.manifest.manifest_digest);
		if (this.externalPorts.has(key)) {
			throw new Error(`External adapter manifest ${validation.manifest.manifest_digest} is already registered`);
		}
		this.externalPorts.set(key, {
			port,
			identity: structuredClone(port.identity),
			binding: structuredClone(binding),
		});
	}

	resolve(kind: ExecutorKind): ExecutorPort | undefined {
		return this.ports.get(kind);
	}

	resolveExternal(kind: ExecutorKind, manifestDigest: string): ExternalExecutorRegistration | undefined {
		const registration = this.externalPorts.get(normalizeDigest(manifestDigest));
		return registration?.identity.kind === kind
			? {
					port: registration.port,
					identity: structuredClone(registration.identity),
					binding: structuredClone(registration.binding),
				}
			: undefined;
	}

	registeredIdentities(): readonly ExecutorIdentity[] {
		return [
			...[...this.ports.values()].map((port) => structuredClone(port.identity)),
			...[...this.externalPorts.values()].map((registration) => structuredClone(registration.identity)),
		];
	}
}
