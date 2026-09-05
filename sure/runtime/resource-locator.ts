import { existsSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";

export interface ResourceLocatorOptions {
	canonicalSkillsRoot?: string;
	legacySkillsRoot?: string;
	semanticBackendRoot?: string;
	environment?: NodeJS.ProcessEnv;
	/** Keep legacy hook wrappers package-local; a missing file must remain missing. */
	packageLocalOnly?: boolean;
}

function relativeResource(resource: string): string {
	const normalized = resource.replaceAll("\\", "/");
	if (!normalized || isAbsolute(normalized) || normalized.split("/").includes("..")) {
		throw new Error(`SURE resource must be a relative non-escaping path: ${resource}`);
	}
	return normalized;
}

function repositoryRoot(packageDir: string): string {
	let current = resolve(packageDir);
	for (;;) {
		if (existsSync(join(current, "sure", "skills")) && existsSync(join(current, "sure", "canonical"))) return current;
		const parent = dirname(current);
		if (parent === current) return resolve(packageDir);
		current = parent;
	}
}

/** Resolve a skill/backend resource using the same logical lookup for Pi and portable hosts. */
export function resolveSkillResource(
	packageDir: string,
	skillId: string,
	resource: string,
	options: ResourceLocatorOptions = {},
): string {
	const relative = relativeResource(resource);
	const env = options.environment ?? process.env;
	const root = repositoryRoot(packageDir);
	const slug = skillId.replaceAll("_", "-");
	const canonicalRoot = options.canonicalSkillsRoot ?? env.SURE_CANONICAL_SKILLS_ROOT;
	const legacyRoot = options.legacySkillsRoot ?? env.SURE_LEGACY_SKILLS_ROOT;
	const backendRoot = options.semanticBackendRoot ?? env.SURE_SEMANTIC_BACKEND_ROOT;
	if (options.packageLocalOnly) return resolve(join(packageDir, relative));
	const candidates = [
		// A hook always receives the concrete skill bundle it is executing. Keep
		// that bundle authoritative so test fixtures and installed distributions
		// can intentionally override repository-level resources.
		join(packageDir, relative),
		...(backendRoot ? [join(backendRoot, slug, relative), join(backendRoot, skillId, relative)] : []),
		...(canonicalRoot ? [join(canonicalRoot, slug, relative)] : []),
		...(legacyRoot ? [join(legacyRoot, skillId, relative)] : []),
		join(root, "sure", "skills", skillId, relative),
		join(root, "sure", "canonical", "skills", slug, relative),
		join(root, "sure", "skills", skillId, relative),
	];
	const found = candidates.find((candidate) => existsSync(candidate));
	if (!found) throw new Error(`SURE resource is not available: ${skillId}/${relative}`);
	return resolve(found);
}

export function resolveSkillScript(
	packageDir: string,
	skillId: string,
	script: string,
	options: ResourceLocatorOptions = {},
): string {
	const resource = script.startsWith("scripts/") ? script : `scripts/${script}`;
	return resolveSkillResource(packageDir, skillId, resource, options);
}
