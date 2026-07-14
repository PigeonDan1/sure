import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import type { SureHookDeclaration, SureHookPoint, SureSkillManifest, SureSkillPackage } from "./types.ts";

const SURE_SKILLS_DIR = ".sure/skills";
const SURE_REPOSITORY_SKILLS_DIR = "sure/skills";
const SURE_MANIFEST_NAME = "sure.skill.json";

export const SURE_COMMANDS = [
	"paper_collect",
	"knowledge_graph",
	"literature_survey",
	"research_idea",
	"novelty_check",
	"run_experiment",
	"data_workflow",
	"model_eval",
	"model_mcp",
	"paper_reproduction",
	"science_gateway",
	"research_discussion",
	"scholar_profile",
	"sure_eval",
	"sure_onboard",
	"sure_feed",
] as const;

const SURE_COMMAND_SET = new Set<string>(SURE_COMMANDS);

const HOOK_POINTS = new Set<SureHookPoint>([
	"pre_start",
	"pre_tool_call",
	"post_tool_result",
	"pre_finish",
	"post_finish",
	"on_error",
]);

export interface SureDiscoveryDiagnostic {
	type: "warning" | "error";
	message: string;
	path?: string;
}

export interface SureDiscoveryResult {
	packages: SureSkillPackage[];
	diagnostics: SureDiscoveryDiagnostic[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readJson(path: string): unknown {
	return JSON.parse(readFileSync(path, "utf-8"));
}

function normalizeCommand(command: string): string {
	return command.startsWith("/") ? command.slice(1) : command;
}

function isPathInside(baseDir: string, candidate: string): boolean {
	const rel = relative(baseDir, candidate);
	return rel === "" || (!rel.startsWith("..") && !rel.startsWith("/") && rel !== "..");
}

function resolvePackagePath(packageDir: string, pathValue: string): string | undefined {
	const resolved = resolve(packageDir, pathValue);
	return isPathInside(packageDir, resolved) ? resolved : undefined;
}

function parseHookDeclaration(value: unknown): SureHookDeclaration | undefined {
	if (!isRecord(value) || typeof value.module !== "string" || value.module.trim() === "") {
		return undefined;
	}
	return {
		module: value.module,
		handler: typeof value.handler === "string" && value.handler.trim() !== "" ? value.handler : undefined,
	};
}

function parseHooks(value: unknown): Partial<Record<SureHookPoint, SureHookDeclaration[]>> | undefined {
	if (!isRecord(value)) {
		return undefined;
	}

	const hooks: Partial<Record<SureHookPoint, SureHookDeclaration[]>> = {};
	for (const [point, declarationValue] of Object.entries(value)) {
		if (!HOOK_POINTS.has(point as SureHookPoint)) {
			continue;
		}

		const declarations = Array.isArray(declarationValue)
			? declarationValue
					.map(parseHookDeclaration)
					.filter((entry): entry is SureHookDeclaration => entry !== undefined)
			: [parseHookDeclaration(declarationValue)].filter(
					(entry): entry is SureHookDeclaration => entry !== undefined,
				);
		if (declarations.length > 0) {
			hooks[point as SureHookPoint] = declarations;
		}
	}

	return Object.keys(hooks).length > 0 ? hooks : undefined;
}

function parseArtifacts(value: unknown): SureSkillManifest["artifacts"] {
	if (!Array.isArray(value)) {
		return undefined;
	}
	return value.filter(isRecord).map((artifact) => ({
		type: typeof artifact.type === "string" ? artifact.type : undefined,
		path: typeof artifact.path === "string" ? artifact.path : undefined,
		required: typeof artifact.required === "boolean" ? artifact.required : undefined,
		description: typeof artifact.description === "string" ? artifact.description : undefined,
	}));
}

function parseStringList(value: unknown): string[] | undefined {
	if (!Array.isArray(value)) {
		return undefined;
	}
	const strings = value.filter((entry): entry is string => typeof entry === "string" && entry.trim() !== "");
	return strings.length > 0 ? strings.map((entry) => entry.trim()) : undefined;
}

function parseUiHints(value: unknown): SureSkillManifest["ui"] {
	if (!isRecord(value)) {
		return undefined;
	}
	const ui = {
		primaryCounters: parseStringList(value.primaryCounters),
		artifactTypes: parseStringList(value.artifactTypes),
		defaultExpandedSections: parseStringList(value.defaultExpandedSections),
	};
	return ui.primaryCounters || ui.artifactTypes || ui.defaultExpandedSections ? ui : undefined;
}

function parseManifest(raw: unknown): SureSkillManifest | undefined {
	if (!isRecord(raw)) {
		return undefined;
	}
	if (typeof raw.name !== "string" || raw.name.trim() === "") {
		return undefined;
	}
	if (typeof raw.command !== "string" || raw.command.trim() === "") {
		return undefined;
	}
	if (typeof raw.prompt !== "string" || raw.prompt.trim() === "") {
		return undefined;
	}

	return {
		name: raw.name.trim(),
		command: normalizeCommand(raw.command.trim()),
		description: typeof raw.description === "string" ? raw.description.trim() : undefined,
		prompt: raw.prompt.trim(),
		hooks: parseHooks(raw.hooks),
		artifacts: parseArtifacts(raw.artifacts),
		ui: parseUiHints(raw.ui),
	};
}

function loadPackage(
	manifestPath: string,
	source: SureSkillPackage["source"],
	sourceRoot: string,
	diagnostics: SureDiscoveryDiagnostic[],
): SureSkillPackage | undefined {
	let parsed: SureSkillManifest | undefined;
	try {
		parsed = parseManifest(readJson(manifestPath));
	} catch (error) {
		diagnostics.push({
			type: "error",
			message: `Invalid Sure skill manifest JSON: ${error instanceof Error ? error.message : String(error)}`,
			path: manifestPath,
		});
		return undefined;
	}

	if (!parsed) {
		diagnostics.push({
			type: "error",
			message: "Invalid Sure skill manifest: expected name, command, and prompt",
			path: manifestPath,
		});
		return undefined;
	}

	const packageDir = dirname(manifestPath);
	const promptPath = resolvePackagePath(packageDir, parsed.prompt);
	if (!promptPath) {
		diagnostics.push({
			type: "error",
			message: "Sure skill prompt path must stay inside the skill package",
			path: manifestPath,
		});
		return undefined;
	}
	if (!existsSync(promptPath)) {
		diagnostics.push({
			type: "error",
			message: "Sure skill prompt path does not exist",
			path: promptPath,
		});
		return undefined;
	}

	for (const declarations of Object.values(parsed.hooks ?? {})) {
		for (const declaration of declarations) {
			const hookPath = resolvePackagePath(packageDir, declaration.module);
			if (!hookPath || !existsSync(hookPath)) {
				diagnostics.push({
					type: "error",
					message: "Sure hook module path must exist inside the skill package",
					path: hookPath ?? join(packageDir, declaration.module),
				});
				return undefined;
			}
		}
	}

	return {
		manifest: parsed,
		manifestPath,
		packageDir,
		promptPath,
		prompt: readFileSync(promptPath, "utf-8").trim(),
		source,
		sourceRoot,
	};
}

function discoverPackagesInRoot(
	root: string,
	source: SureSkillPackage["source"],
	diagnostics: SureDiscoveryDiagnostic[],
): SureSkillPackage[] {
	const packages: SureSkillPackage[] = [];
	if (!existsSync(root)) {
		return packages;
	}

	for (const entry of readdirSync(root)) {
		const packageDir = join(root, entry);
		if (!statSync(packageDir).isDirectory()) {
			continue;
		}
		const manifestPath = join(packageDir, SURE_MANIFEST_NAME);
		if (!existsSync(manifestPath)) {
			continue;
		}
		const skillPackage = loadPackage(manifestPath, source, root, diagnostics);
		if (skillPackage) {
			packages.push(skillPackage);
		}
	}
	return packages;
}

function filterDuplicateCommands(
	packages: SureSkillPackage[],
	diagnostics: SureDiscoveryDiagnostic[],
): SureSkillPackage[] {
	const commandOwners = new Map<string, SureSkillPackage>();
	const validPackages: SureSkillPackage[] = [];
	const duplicateCommands = new Set<string>();
	for (const skillPackage of packages) {
		if (!SURE_COMMAND_SET.has(skillPackage.manifest.command)) {
			diagnostics.push({
				type: "error",
				message: `Unknown Sure skill command "/${skillPackage.manifest.command}"`,
				path: skillPackage.manifestPath,
			});
			continue;
		}
		const existing = commandOwners.get(skillPackage.manifest.command);
		if (existing) {
			duplicateCommands.add(skillPackage.manifest.command);
			diagnostics.push({
				type: "error",
				message: `Duplicate Sure skill command "/${skillPackage.manifest.command}" also declared by ${existing.manifestPath}`,
				path: skillPackage.manifestPath,
			});
			continue;
		}
		commandOwners.set(skillPackage.manifest.command, skillPackage);
		validPackages.push(skillPackage);
	}

	return validPackages.filter((skillPackage) => !duplicateCommands.has(skillPackage.manifest.command));
}

export function discoverSureSkillPackages(cwd: string): SureDiscoveryResult {
	const diagnostics: SureDiscoveryDiagnostic[] = [];
	const projectPackages = filterDuplicateCommands(
		discoverPackagesInRoot(join(cwd, SURE_SKILLS_DIR), "project", diagnostics),
		diagnostics,
	);
	const repositoryPackages = filterDuplicateCommands(
		discoverPackagesInRoot(join(cwd, SURE_REPOSITORY_SKILLS_DIR), "repository", diagnostics),
		diagnostics,
	);

	const projectCommands = new Set(projectPackages.map((skillPackage) => skillPackage.manifest.command));
	const packages = [
		...projectPackages,
		...repositoryPackages.filter((skillPackage) => {
			if (!projectCommands.has(skillPackage.manifest.command)) {
				return true;
			}
			diagnostics.push({
				type: "warning",
				message: `Repository Sure skill "/${skillPackage.manifest.command}" is overridden by project .sure/skills.`,
				path: skillPackage.manifestPath,
			});
			return false;
		}),
	];

	return {
		packages,
		diagnostics,
	};
}

export function resolveSurePackagePath(packageDir: string, pathValue: string): string | undefined {
	return resolvePackagePath(packageDir, pathValue);
}
