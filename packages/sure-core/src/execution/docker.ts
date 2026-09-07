import type { ExecutionReceipt, ExecutionRequest, JsonValue } from "../contracts/types.ts";

/**
 * The host-neutral portion of a Docker execution request.
 *
 * The request entrypoint remains the command executed inside the image.  The
 * host adapter is responsible for turning this data into `docker run` argv;
 * keeping the shape here prevents each harness from inventing its own mount
 * and environment encoding.
 */
export interface DockerMountSpec {
	source: string;
	target: string;
	read_only: boolean;
}

export interface DockerRuntimeSpec {
	image: string;
	mounts: readonly DockerMountSpec[];
	environment: Readonly<Record<string, string>>;
	working_directory?: string;
	network?: string;
	image_digest?: string;
}

/**
 * Return the content digest embedded in an image reference, if one exists.
 * Docker references may contain a registry port and arbitrary repository
 * components, so the digest is deliberately parsed only from the terminal
 * `@sha256:<hex>` component.
 */
export function dockerImageDigest(image: string): string | undefined {
	const match = /@sha256:(?<hex>[0-9a-f]{64})$/i.exec(image);
	return match?.groups?.hex === undefined ? undefined : `sha256:${match.groups.hex.toLowerCase()}`;
}

export function dockerImageIsDigestPinned(image: string): boolean {
	return dockerImageDigest(image) !== undefined;
}

export interface DockerRuntimeSpecValidation {
	valid: boolean;
	errors: readonly string[];
	spec?: DockerRuntimeSpec;
}

const DIGEST = /^(?:sha256:)?[0-9a-f]{64}$/i;
const ENVIRONMENT_KEY = /^[A-Za-z_][A-Za-z0-9_]*$/;

function object(value: JsonValue | undefined): value is { [key: string]: JsonValue } {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nonEmptyString(value: JsonValue | undefined, field: string, errors: string[]): string | undefined {
	if (typeof value !== "string" || value.trim() === "") {
		errors.push(`${field} must be a non-empty string`);
		return undefined;
	}
	return value;
}

function absolutePosixPath(value: JsonValue | undefined, field: string, errors: string[]): string | undefined {
	const candidate = nonEmptyString(value, field, errors);
	if (candidate === undefined) return undefined;
	if (!candidate.startsWith("/") || candidate.includes("\\") || candidate.split("/").includes("..")) {
		errors.push(`${field} must be an absolute non-escaping POSIX path`);
		return undefined;
	}
	return candidate;
}

/**
 * Parse the deliberately small Docker runtime contract carried in
 * `ExecutionRequest.runtime_requirements`.
 *
 * This is syntax/admission validation only.  Image existence, daemon health,
 * and mount containment are checked by the selected host executor.
 */
export function parseDockerRuntimeRequirements(
	runtimeRequirements: Record<string, JsonValue>,
): DockerRuntimeSpecValidation {
	const errors: string[] = [];
	const image = nonEmptyString(runtimeRequirements.docker_image, "runtime_requirements.docker_image", errors);
	const rawMounts = runtimeRequirements.docker_mounts;
	const mounts: DockerMountSpec[] = [];
	if (rawMounts === undefined) {
		errors.push("runtime_requirements.docker_mounts must be an array");
	} else if (!Array.isArray(rawMounts)) {
		errors.push("runtime_requirements.docker_mounts must be an array");
	} else {
		for (const [index, rawMount] of rawMounts.entries()) {
			const field = `runtime_requirements.docker_mounts[${index}]`;
			if (!object(rawMount)) {
				errors.push(`${field} must be an object`);
				continue;
			}
			const source = absoluteHostPath(rawMount.source, `${field}.source`, errors);
			const target = absolutePosixPath(rawMount.target, `${field}.target`, errors);
			if (typeof rawMount.read_only !== "boolean") {
				errors.push(`${field}.read_only must be boolean`);
			}
			if (source !== undefined && target !== undefined && typeof rawMount.read_only === "boolean") {
				mounts.push({ source, target, read_only: rawMount.read_only });
			}
		}
	}

	const rawEnvironment = runtimeRequirements.docker_env;
	const environment: Record<string, string> = {};
	if (rawEnvironment !== undefined) {
		if (!object(rawEnvironment)) {
			errors.push("runtime_requirements.docker_env must be an object");
		} else {
			for (const [key, value] of Object.entries(rawEnvironment)) {
				if (!ENVIRONMENT_KEY.test(key)) errors.push(`runtime_requirements.docker_env key is invalid: ${key}`);
				if (typeof value !== "string") {
					errors.push(`runtime_requirements.docker_env.${key} must be a string`);
				} else {
					environment[key] = value;
				}
			}
		}
	}

	let workingDirectory: string | undefined;
	if (runtimeRequirements.docker_workdir !== undefined) {
		workingDirectory = absolutePosixPath(
			runtimeRequirements.docker_workdir,
			"runtime_requirements.docker_workdir",
			errors,
		);
	}

	let network: string | undefined;
	if (runtimeRequirements.docker_network !== undefined) {
		network = nonEmptyString(runtimeRequirements.docker_network, "runtime_requirements.docker_network", errors);
		if (network !== undefined && /[\s\\]/.test(network)) {
			errors.push("runtime_requirements.docker_network must not contain whitespace or backslashes");
		}
	}

	let imageDigest: string | undefined;
	const embeddedImageDigest = image === undefined ? undefined : dockerImageDigest(image);
	if (runtimeRequirements.docker_image_digest !== undefined) {
		imageDigest = nonEmptyString(
			runtimeRequirements.docker_image_digest,
			"runtime_requirements.docker_image_digest",
			errors,
		);
		if (imageDigest !== undefined && !DIGEST.test(imageDigest)) {
			errors.push("runtime_requirements.docker_image_digest must be a SHA-256 digest");
		}
		if (
			imageDigest !== undefined &&
			embeddedImageDigest !== undefined &&
			imageDigest.replace(/^sha256:/, "").toLowerCase() !== embeddedImageDigest.replace(/^sha256:/, "")
		) {
			errors.push("runtime_requirements.docker_image_digest does not match the digest in docker_image");
		}
	}
	if (imageDigest === undefined && embeddedImageDigest !== undefined) imageDigest = embeddedImageDigest;
	if (errors.length > 0 || image === undefined) return { valid: false, errors };
	return {
		valid: true,
		errors: [],
		spec: {
			image,
			mounts,
			environment,
			...(workingDirectory === undefined ? {} : { working_directory: workingDirectory }),
			...(network === undefined ? {} : { network }),
			...(imageDigest === undefined ? {} : { image_digest: imageDigest }),
		},
	};
}

/**
 * Formal Docker evidence must bind the declared image to an independent host
 * probe.  The executor writes these details into the required Docker
 * capability evidence; a receipt that merely says `SUCCEEDED` is insufficient.
 */
export function validateDockerRuntimeEvidence(request: ExecutionRequest, receipt: ExecutionReceipt): readonly string[] {
	if (request.runtime_requirements.executor_kind !== "docker") return [];
	const parsed = parseDockerRuntimeRequirements(request.runtime_requirements);
	if (!parsed.valid || parsed.spec === undefined) return parsed.errors;
	const evidence = receipt.capability_evidence.find(
		(candidate) =>
			candidate.capability_id === "sure.execution.docker" && candidate.capability_class === "execution_capability",
	);
	if (evidence?.status !== "AVAILABLE") {
		return ["Docker capability evidence is missing or not AVAILABLE"];
	}
	const details = evidence.details;
	if (details === undefined || details.image_verified !== true) {
		return ["Docker capability evidence is not independently image-verified"];
	}
	if (details.image_ref !== parsed.spec.image) {
		return ["Docker capability evidence image_ref does not match the execution request"];
	}
	const observedDigest = details.image_digest;
	if (typeof observedDigest !== "string" || !DIGEST.test(observedDigest)) {
		return ["Docker capability evidence is missing a valid image digest"];
	}
	if (
		parsed.spec.image_digest === undefined ||
		observedDigest.replace(/^sha256:/, "").toLowerCase() !==
			parsed.spec.image_digest.replace(/^sha256:/, "").toLowerCase()
	) {
		return ["Docker capability evidence image digest does not match the execution request"];
	}
	return [];
}

function absoluteHostPath(value: JsonValue | undefined, field: string, errors: string[]): string | undefined {
	const candidate = nonEmptyString(value, field, errors);
	if (candidate === undefined) return undefined;
	if (!candidate.startsWith("/") || candidate.includes("\\") || candidate.split("/").includes("..")) {
		errors.push(`${field} must be an absolute non-escaping host path`);
		return undefined;
	}
	return candidate;
}
