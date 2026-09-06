import { createHash } from "node:crypto";
import { existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { canonicalJson, canonicalJsonDigest, sha256Hex } from "../packages/sure-core/src/contracts/canonical-json.ts";
import type { JsonValue } from "../packages/sure-core/src/contracts/types.ts";
import { ValidatorRegistry, type ValidatorRegistrySnapshot } from "../packages/sure-core/src/validation/index.ts";
import { CANONICAL_SEMANTIC_BACKENDS } from "../sure/canonical/shared/evaluation/registry.ts";
import { CANONICAL_SKILLS } from "../sure/canonical/skills/index.ts";
import type { CanonicalSkillDefinition } from "../sure/canonical/types.ts";
import { canonicalValidatorRegistry } from "../sure/canonical/validators/index.ts";

const repositoryRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const canonicalSkillsRoot = join(repositoryRoot, "sure", "canonical", "skills");
const generatedPiRoot = join(repositoryRoot, "sure", "generated", "pi", "skills");
const portableRoot = join(repositoryRoot, "sure", "dist", "agent-skills");
const GENERATED_MARKER = ".sure-generated";
const UNSAFE_PORTABLE_TEXT =
	/@earendil-works\/pi-coding-agent|HARNESS_PYTHON_BIN|sure_finish|sure_update_state|pre_start|pre_tool_call|post_tool_result|\.\.\/sure_[a-z_]+|sure\/skills\/sure_/;

interface GeneratedFile {
	path: string;
	content: Buffer;
}

function asJson(value: unknown): JsonValue {
	return value as JsonValue;
}

function jsonFile(value: unknown): Buffer {
	return Buffer.from(`${canonicalJson(asJson(value))}\n`, "utf8");
}

function sha256(value: Uint8Array): string {
	return createHash("sha256").update(value).digest("hex");
}

function materializedValidatorRegistry(): ValidatorRegistrySnapshot {
	const descriptors = canonicalValidatorRegistry()
		.list()
		.map((descriptor) => {
			if (descriptor.resource_path === undefined) return descriptor;
			const path = join(canonicalSkillsRoot, descriptor.resource_path);
			if (!existsSync(path) || !lstatSync(path).isFile()) {
				throw new Error(`Canonical validator resource is missing: ${descriptor.id} -> ${path}`);
			}
			return { ...descriptor, resource_digest: `sha256:${sha256(readFileSync(path))}` };
		});
	return new ValidatorRegistry(descriptors).snapshot();
}

function allFiles(root: string, prefix = ""): string[] {
	if (!existsSync(root)) return [];
	const entries = readdirSync(root, { withFileTypes: true }).sort((left, right) =>
		left.name.localeCompare(right.name),
	);
	const files: string[] = [];
	for (const entry of entries) {
		const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name;
		const absolutePath = join(root, entry.name);
		if (entry.isDirectory()) {
			if (entry.name === "__pycache__" || entry.name === "node_modules") continue;
			files.push(...allFiles(absolutePath, relativePath));
		} else if (entry.isFile()) {
			if (
				entry.name.endsWith(".pyc") ||
				entry.name.endsWith(".js") ||
				entry.name.endsWith(".d.ts") ||
				entry.name.endsWith(".map")
			)
				continue;
			files.push(relativePath);
		}
	}
	return files;
}

function fileDigest(entries: readonly { path: string; content: Uint8Array }[]): string {
	const digestInput = [...entries]
		.sort((left, right) => left.path.localeCompare(right.path))
		.map((entry) => `${entry.path}\0${sha256(entry.content)}`)
		.join("\n");
	return sha256Hex(digestInput);
}

/** Hash a complete backend tree with the same path/content ordering on every host. */
function backendTreeDigest(root: string): string {
	const paths = allFiles(root).sort((left, right) => (left < right ? -1 : left > right ? 1 : 0));
	const rows = paths.map((path) => `${path}\0sha256:${sha256(readFileSync(join(root, path)))}`).join("\n");
	return `sha256:${sha256Hex(rows)}`;
}

interface MaterializedSemanticBackendManifest {
	schema: "sure.semantic.backend.manifest.v1";
	registry_digest: string;
	bundles: JsonValue[];
}

function materializedSemanticBackendManifest(): MaterializedSemanticBackendManifest {
	const bundles = CANONICAL_SEMANTIC_BACKENDS.map((bundle) => {
		const canonicalRoot = join(canonicalSkillsRoot, bundle.canonical_root);
		const legacyRoot = join(repositoryRoot, "sure", bundle.legacy_root);
		const canonicalTree = existsSync(canonicalRoot) ? backendTreeDigest(canonicalRoot) : undefined;
		const legacyTree = existsSync(legacyRoot) ? backendTreeDigest(legacyRoot) : undefined;
		const operations = bundle.operations.map((operation) => {
			const canonicalPath = join(canonicalRoot, operation.entrypoint);
			const legacyPath = join(legacyRoot, operation.entrypoint);
			if (!existsSync(canonicalPath) || !lstatSync(canonicalPath).isFile()) {
				throw new Error(
					`Canonical semantic backend resource is missing: ${operation.operation_id} -> ${canonicalPath}`,
				);
			}
			if (!existsSync(legacyPath) || !lstatSync(legacyPath).isFile()) {
				throw new Error(`Legacy semantic backend resource is missing: ${operation.operation_id} -> ${legacyPath}`);
			}
			return {
				...operation,
				canonical_resource_digest: `sha256:${sha256(readFileSync(canonicalPath))}`,
				legacy_resource_digest: `sha256:${sha256(readFileSync(legacyPath))}`,
			};
		});
		return {
			...bundle,
			canonical_tree_digest: canonicalTree,
			legacy_tree_digest: legacyTree,
			operations,
		};
	});
	const unsigned = { schema: "sure.semantic.backend.manifest.v1", bundles } as unknown as JsonValue;
	return {
		schema: "sure.semantic.backend.manifest.v1",
		registry_digest: canonicalJsonDigest(unsigned),
		bundles: bundles as unknown as JsonValue[],
	};
}

function readCanonicalResourceFiles(skill: CanonicalSkillDefinition): { path: string; content: Buffer }[] {
	const root = join(canonicalSkillsRoot, skill.distribution_slug);
	const files: { path: string; content: Buffer }[] = [];
	for (const directory of skill.resources.directories) {
		for (const path of allFiles(join(root, directory), directory)) {
			files.push({ path, content: readFileSync(join(root, path)) });
		}
	}
	return files;
}

function portableResourceFiles(skill: CanonicalSkillDefinition): {
	files: { path: string; content: Buffer }[];
	omitted: string[];
} {
	const selected: { path: string; content: Buffer }[] = [];
	const omitted: string[] = [];
	for (const entry of readCanonicalResourceFiles(skill)) {
		if (entry.path.startsWith("scripts/")) {
			omitted.push(entry.path);
			continue;
		}
		const text = entry.content.toString("utf8");
		if (UNSAFE_PORTABLE_TEXT.test(text)) {
			omitted.push(entry.path);
			continue;
		}
		selected.push(entry);
	}
	return { files: selected, omitted };
}

function workflowTable(skill: CanonicalSkillDefinition): string {
	return skill.workflow.branches
		.map(
			(branch) =>
				`### Branch ${branch.id}\n\n${branch.units
					.map((unit, index) => `${index + 1}. \`${unit.id}\` -> \`${unit.produces}\` (${unit.kind}).`)
					.join("\n")}`,
		)
		.join("\n\n");
}

function artifactTable(skill: CanonicalSkillDefinition): string {
	const rows = skill.published_artifacts.map(
		(artifact) =>
			`| \`${artifact.type}\` | ${artifact.path ? `\`${artifact.path}\`` : "(declared by producer)"} | ${artifact.required ? "yes" : "no"} |`,
	);
	return ["| Artifact | Path | Required |", "| --- | --- | --- |", ...rows].join("\n");
}

function portableSkillMarkdown(skill: CanonicalSkillDefinition): string {
	return [
		"---",
		`name: ${skill.distribution_slug}`,
		`description: ${JSON.stringify(skill.description)}`,
		"---",
		"",
		`# ${skill.display_name}`,
		"",
		`This portable skill coordinates ${skill.description.toLowerCase()}`,
		"",
		readFileSync(join(canonicalSkillsRoot, skill.distribution_slug, skill.instructions.common_path), "utf8").trim(),
		"",
		readFileSync(join(canonicalSkillsRoot, skill.distribution_slug, skill.instructions.portable_path), "utf8").trim(),
		"",
		"## Workflow reference",
		"",
		workflowTable(skill),
		"",
		"## Canonical artifact contract",
		"",
		artifactTable(skill),
		"",
		"## References",
		"",
		"Use only the bundled schemas and references that are present in this distribution. Semantic validators and execution are resolved by SURE Core from the pinned backend manifest; an agent must not substitute an unregistered checker.",
		"",
	].join("\n");
}

function yamlString(value: string): string {
	return JSON.stringify(value);
}

function openAiYaml(skill: CanonicalSkillDefinition): string {
	const display = skill.display_name;
	const short = `${skill.display_name.replace(/^SURE /, "")} workflow for reproducible model evaluation`;
	const prompt = `Use $${skill.distribution_slug} to ${skill.description.toLowerCase()}`;
	return [
		"interface:",
		`  display_name: ${yamlString(display)}`,
		`  short_description: ${yamlString(short.slice(0, 64))}`,
		`  default_prompt: ${yamlString(prompt)}`,
		"policy:",
		"  allow_implicit_invocation: true",
		"",
	].join("\n");
}

function piManifest(skill: CanonicalSkillDefinition): Record<string, unknown> {
	return {
		name: skill.pi.name,
		command: skill.pi.command,
		description: skill.pi.description,
		prompt: skill.pi.prompt,
		hooks: skill.pi.hooks,
		artifacts: skill.published_artifacts.map(({ type, path, required, description }) => ({
			type,
			...(path === undefined ? {} : { path }),
			required,
			description,
		})),
		ui: skill.pi.ui,
	};
}

function sanitizePortableText(value: string): string {
	return value
		.replaceAll(/\/sure_[a-z_]+/g, "SURE workflow")
		.replaceAll("HARNESS_PYTHON_BIN", "the pinned harness runtime")
		.replaceAll("@earendil-works/pi-coding-agent", "the host adapter");
}

function portableDefinition(skill: CanonicalSkillDefinition): Record<string, unknown> {
	return {
		schema: skill.schema,
		skill_id: skill.skill_id,
		command_id: skill.command_id,
		distribution_slug: skill.distribution_slug,
		display_name: skill.display_name,
		description: sanitizePortableText(skill.description),
		workflow: skill.workflow,
		unit_outputs: skill.unit_outputs,
		internal_evidence: skill.internal_evidence,
		published_artifacts: skill.published_artifacts.map((artifact) => ({
			...artifact,
			description: sanitizePortableText(artifact.description),
		})),
		capabilities: skill.capabilities,
		semantic_validators: skill.semantic_validators,
		instructions: {
			common_path: skill.instructions.common_path,
			portable_path: skill.instructions.portable_path,
		},
		resources: { directories: skill.resources.directories },
	};
}

function lockFor(
	skill: CanonicalSkillDefinition,
	host: "pi" | "portable",
	resources: readonly { path: string; content: Uint8Array }[],
	omitted: readonly string[],
	semanticBackendManifest: MaterializedSemanticBackendManifest,
): Record<string, unknown> {
	const definitionDigest = canonicalJsonDigest(asJson(skill));
	const workflowDigest = canonicalJsonDigest(asJson(skill.workflow));
	const resourceDigest = fileDigest(resources);
	const backendDigest = fileDigest(
		readCanonicalResourceFiles(skill).filter((entry) => entry.path.startsWith("scripts/")),
	);
	const validatorRegistryDigest = materializedValidatorRegistry().digest;
	return {
		schema: "sure.skill.generation.lock.v1",
		host,
		skill_id: skill.skill_id,
		command_id: skill.command_id,
		distribution_slug: skill.distribution_slug,
		definition_digest: definitionDigest,
		workflow_digest: workflowDigest,
		resource_digest: resourceDigest,
		semantic_backend_digest: backendDigest,
		validator_registry_digest: validatorRegistryDigest,
		semantic_backend_registry_digest: semanticBackendManifest.registry_digest,
		core_package_version: "0.80.3",
		portable_omitted_resources: [...omitted].sort(),
	};
}

function hookFacade(skill: CanonicalSkillDefinition): Buffer {
	const oldRoot = skill.resources.legacy_root ?? `sure/skills/${skill.skill_id}`;
	const generatedHooks = join(generatedPiRoot, skill.skill_id, "hooks");
	const oldHooks = join(repositoryRoot, oldRoot, "hooks");
	const relativeOld = relative(generatedHooks, oldHooks).replaceAll("\\", "/");
	const importPath = `${relativeOld || "."}/index.ts`;
	return Buffer.from(
		`// Generated Pi compatibility facade. Lifecycle authority remains the Pi adapter.\nexport * from ${JSON.stringify(importPath)};\n`,
		"utf8",
	);
}

function buildHostFiles(
	skill: CanonicalSkillDefinition,
	host: "pi" | "portable",
	semanticBackendManifest: MaterializedSemanticBackendManifest,
): { files: GeneratedFile[]; lock: Record<string, unknown> } {
	const canonicalRoot = join(canonicalSkillsRoot, skill.distribution_slug);
	const resourceSet =
		host === "portable"
			? portableResourceFiles(skill)
			: { files: readCanonicalResourceFiles(skill), omitted: [] as string[] };
	const root = host === "pi" ? join(generatedPiRoot, skill.skill_id) : join(portableRoot, skill.distribution_slug);
	const files: GeneratedFile[] = [];
	if (host === "pi") {
		files.push({
			path: join(root, "SKILL.md"),
			content: readFileSync(join(canonicalRoot, skill.instructions.pi_path)),
		});
		files.push({ path: join(root, "sure.skill.json"), content: jsonFile(piManifest(skill)) });
		files.push({ path: join(root, "hooks", "index.ts"), content: hookFacade(skill) });
	} else {
		files.push({ path: join(root, "SKILL.md"), content: Buffer.from(portableSkillMarkdown(skill), "utf8") });
		files.push({ path: join(root, "agents", "openai.yaml"), content: Buffer.from(openAiYaml(skill), "utf8") });
		files.push({
			path: join(root, "semantic-backends.json"),
			content: jsonFile({
				schema: "sure.semantic.backends.v1",
				validators: skill.semantic_validators,
				resolution: "sure-core-registry",
				registry_digest: semanticBackendManifest.registry_digest,
				bundles: semanticBackendManifest.bundles,
			}),
		});
		files.push({
			path: join(root, "references", "README.md"),
			content: Buffer.from(
				`# Bundled references\n\nThis directory contains host-neutral references selected from the canonical skill. ${resourceSet.omitted.length} legacy host/backend files are intentionally resolved by the pinned SURE Core registry instead of copied into a portable skill.\n`,
				"utf8",
			),
		});
	}
	if (host === "pi") {
		files.push({
			path: join(root, "semantic-backends.json"),
			content: jsonFile({
				schema: "sure.semantic.backends.v1",
				validators: skill.semantic_validators,
				resolution: "sure-core-registry",
				registry_digest: semanticBackendManifest.registry_digest,
				bundles: semanticBackendManifest.bundles,
			}),
		});
	}
	const registry = materializedValidatorRegistry();
	files.push({
		path: join(root, "validator-registry.json"),
		content: jsonFile({
			schema: registry.schema,
			digest: registry.digest,
			validators: registry.validators.filter(
				(descriptor) => descriptor.skill_id === undefined || descriptor.skill_id === skill.skill_id,
			),
		}),
	});
	for (const resource of resourceSet.files) files.push({ path: join(root, resource.path), content: resource.content });
	const lock = lockFor(skill, host, resourceSet.files, resourceSet.omitted, semanticBackendManifest);
	files.push({ path: join(root, "generation.lock.json"), content: jsonFile(lock) });
	files.push({
		path: join(root, "canonical-definition.json"),
		content: jsonFile(host === "portable" ? portableDefinition(skill) : skill),
	});
	return { files, lock };
}

function expectedFiles(): GeneratedFile[] {
	const files: GeneratedFile[] = [];
	const semanticBackendManifest = materializedSemanticBackendManifest();
	files.push({
		path: join(repositoryRoot, "sure", "canonical", "shared", "evaluation", "backend-manifest.json"),
		content: jsonFile(semanticBackendManifest),
	});
	for (const skill of CANONICAL_SKILLS) {
		files.push(
			...buildHostFiles(skill, "pi", semanticBackendManifest).files,
			...buildHostFiles(skill, "portable", semanticBackendManifest).files,
		);
	}
	const registry = CANONICAL_SKILLS.map((skill) => ({
		skill_id: skill.skill_id,
		command_id: skill.command_id,
		pi_command: skill.pi.command,
		portable_slug: skill.distribution_slug,
		workflow_digest: canonicalJsonDigest(asJson(skill.workflow)),
	}));
	files.push({
		path: join(generatedPiRoot, "registry.json"),
		content: jsonFile({ schema: "sure.command.registry.v1", entries: registry }),
	});
	files.push({
		path: join(portableRoot, "registry.json"),
		content: jsonFile({ schema: "sure.command.registry.v1", entries: registry }),
	});
	const validatorRegistry = materializedValidatorRegistry();
	files.push({
		path: join(repositoryRoot, "sure", "canonical", "validators", "registry.json"),
		content: jsonFile(validatorRegistry),
	});
	const memoryEntries = ["sure_onboard", "sure_infer", "sure_eval", "sure_trans", "sure_feed"]
		.map((skillId) => CANONICAL_SKILLS.find((skill) => skill.skill_id === skillId))
		.filter((skill): skill is CanonicalSkillDefinition => skill !== undefined)
		.map((skill) => [
			skill.skill_id,
			skill.workflow.branches.flatMap((branch) => branch.units.map((unit) => unit.id)),
		]);
	const memoryDocument = { schema: "sure.memory.units.v1", skills: Object.fromEntries(memoryEntries) };
	files.push({
		path: join(repositoryRoot, "sure", "runtime", "memory", "units.json"),
		content: jsonFile(memoryDocument),
	});
	files.push({
		path: join(repositoryRoot, "sure", "runtime", "memory", "units.generation.lock.json"),
		content: jsonFile({
			schema: "sure.memory.units.generation.lock.v1",
			workflow_digests: Object.fromEntries(
				CANONICAL_SKILLS.filter((skill) => skill.skill_id !== "sure_approve").map((skill) => [
					skill.skill_id,
					canonicalJsonDigest(asJson(skill.workflow)),
				]),
			),
		}),
	});
	return files;
}

function markerPath(root: string): string {
	return join(root, GENERATED_MARKER);
}

function writeGenerated(files: readonly GeneratedFile[]): void {
	const roots = [generatedPiRoot, portableRoot];
	for (const root of roots) {
		if (existsSync(root)) {
			if (!existsSync(markerPath(root))) throw new Error(`Refusing to replace unmarked generated root: ${root}`);
			rmSync(root, { recursive: true, force: true });
		}
		mkdirSync(root, { recursive: true });
		writeFileSync(markerPath(root), "sure generated; do not edit\n", "utf8");
	}
	for (const file of files) {
		mkdirSync(dirname(file.path), { recursive: true });
		writeFileSync(file.path, file.content);
	}
}

function actualFiles(root: string): string[] {
	return allFiles(root).filter((path) => path !== GENERATED_MARKER);
}

function checkGenerated(files: readonly GeneratedFile[]): void {
	const expected = new Map(files.map((file) => [file.path, file.content]));
	const problems: string[] = [];
	for (const [path, content] of expected) {
		if (!existsSync(path)) {
			problems.push(`missing ${relative(repositoryRoot, path)}`);
			continue;
		}
		if (!lstatSync(path).isFile() || !readFileSync(path).equals(content))
			problems.push(`stale ${relative(repositoryRoot, path)}`);
	}
	for (const root of [generatedPiRoot, portableRoot]) {
		for (const path of actualFiles(root)) {
			const absolute = join(root, path);
			if (!expected.has(absolute)) problems.push(`unexpected ${relative(repositoryRoot, absolute)}`);
		}
	}
	if (problems.length > 0) throw new Error(`Generated SURE skill outputs are stale:\n${problems.join("\n")}`);
}

const files = expectedFiles();
if (process.argv.includes("--check")) {
	checkGenerated(files);
	console.log(`SURE skill generation is current (${files.length} files).`);
} else {
	writeGenerated(files);
	console.log(`Generated ${files.length} SURE Pi/portable skill files.`);
}
