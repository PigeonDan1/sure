import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type { JsonValue } from "../contracts/types.ts";
import type { ValidatorDescriptor, ValidatorRegistrySnapshot } from "./types.ts";

const ID_PATTERN = /^[a-z0-9][a-z0-9._:-]*$/;

function assertDescriptor(descriptor: ValidatorDescriptor): ValidatorDescriptor {
	if (!ID_PATTERN.test(descriptor.id)) throw new Error(`Invalid validator id: ${descriptor.id}`);
	if (descriptor.backend_operation_id !== undefined && !ID_PATTERN.test(descriptor.backend_operation_id)) {
		throw new Error(`Invalid backend operation id: ${descriptor.backend_operation_id}`);
	}
	if (!descriptor.version.trim()) throw new Error(`Validator ${descriptor.id} needs a version.`);
	if (descriptor.resource_path !== undefined) {
		if (descriptor.resource_path.startsWith("/") || descriptor.resource_path.split("/").includes("..")) {
			throw new Error(`Validator ${descriptor.id} resource path must stay relative.`);
		}
	}
	return descriptor;
}

/** Immutable, host-neutral registry. It describes validators; it never executes them. */
export class ValidatorRegistry {
	private readonly descriptors: Map<string, ValidatorDescriptor>;

	constructor(descriptors: readonly ValidatorDescriptor[] = []) {
		this.descriptors = new Map();
		for (const descriptor of descriptors) this.register(descriptor);
	}

	register(descriptor: ValidatorDescriptor): void {
		const valid = assertDescriptor(descriptor);
		if (this.descriptors.has(valid.id)) throw new Error(`Validator already registered: ${valid.id}`);
		this.descriptors.set(valid.id, { ...valid });
	}

	get(id: string): ValidatorDescriptor | undefined {
		const descriptor = this.descriptors.get(id);
		return descriptor === undefined ? undefined : { ...descriptor };
	}

	require(id: string): ValidatorDescriptor {
		const descriptor = this.get(id);
		if (!descriptor) throw new Error(`Validator is not registered: ${id}`);
		return descriptor;
	}

	list(): readonly ValidatorDescriptor[] {
		return [...this.descriptors.values()].sort((left, right) => left.id.localeCompare(right.id));
	}

	snapshot(): ValidatorRegistrySnapshot {
		const validators = this.list();
		return {
			schema: "sure.validator.registry.v1",
			validators,
			digest: canonicalJsonDigest({ schema: "sure.validator.registry.v1", validators } as unknown as JsonValue),
		};
	}
}
