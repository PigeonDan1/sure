import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, onTestFinished } from "vitest";
import { SURE_WORKFLOWS } from "../packages/coding-agent/src/core/sure/generated-workflows.ts";
import { canonicalJson, canonicalJsonDigest } from "../packages/sure-core/src/contracts/canonical-json.ts";
import { validateJsonSchema } from "../packages/sure-core/src/contracts/schema.ts";
import type { JsonValue } from "../packages/sure-core/src/contracts/types.ts";
import { verifyPortableRuntime } from "../packages/sure-core/src/evaluation/portable-runtime.ts";
import { resolveSemanticBackendOperation } from "../packages/sure-core/src/evaluation/semantic-backend.ts";
import { executorRegistrySnapshot } from "../packages/sure-core/src/execution/registry.ts";
import { CANONICAL_MEMORY_CONTRACT } from "../sure/canonical/shared/memory-contract.ts";
import { CANONICAL_SKILLS } from "../sure/canonical/skills/index.ts";
import { canonicalValidatorRegistry } from "../sure/canonical/validators/index.ts";

const repositoryRoot = join(fileURLToPath(new URL("..", import.meta.url)));

function readJson(path: string): Record<string, unknown> {
	return JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
}

function asJson(value: unknown): JsonValue {
	return value as JsonValue;
}

describe("canonical SURE skill generation", () => {
	it("compiles canonical workflows into the Pi package boundary", () => {
		for (const skill of CANONICAL_SKILLS) {
			expect(SURE_WORKFLOWS[skill.skill_id as keyof typeof SURE_WORKFLOWS]).toEqual(skill.workflow);
		}
		const controller = readFileSync(
			join(repositoryRoot, "packages/coding-agent/src/core/sure/controller.ts"),
			"utf8",
		);
		expect(controller).not.toMatch(/sure\/(?:core|canonical|runtime|site)/);
	});

	it("has one stable command mapping per skill and matching host workflow digests", () => {
		const canonicalRegistry = readJson(join(repositoryRoot, "sure/canonical/validators/registry.json"));
		const runtimeLock = readJson(join(repositoryRoot, "sure/dist/portable-runtime/runtime-support.lock.json"));
		expect(CANONICAL_SKILLS).toHaveLength(6);
		expect(new Set(CANONICAL_SKILLS.map((skill) => skill.command_id)).size).toBe(6);
		for (const skill of CANONICAL_SKILLS) {
			const piLock = readJson(
				join(repositoryRoot, "sure/generated/pi/skills", skill.skill_id, "generation.lock.json"),
			);
			const portableLock = readJson(
				join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "generation.lock.json"),
			);
			expect(piLock.workflow_digest).toBe(portableLock.workflow_digest);
			expect(piLock.definition_digest).toBe(portableLock.definition_digest);
			expect(piLock.semantic_backend_digest).toBe(portableLock.semantic_backend_digest);
			expect(piLock.validator_registry_digest).toBe(canonicalRegistry.digest);
			expect(portableLock.validator_registry_digest).toBe(piLock.validator_registry_digest);
			expect(piLock.semantic_runtime_digest).toBe(runtimeLock.runtime_digest);
			expect(portableLock.semantic_runtime_digest).toBe(runtimeLock.runtime_digest);
			expect(piLock.workflow_digest).toBe(canonicalJsonDigest(asJson(skill.workflow)));
		}
	});

	it("ships the same validator identities to both hosts", () => {
		const canonicalFile = readJson(join(repositoryRoot, "sure/canonical/validators/registry.json"));
		const declared = canonicalValidatorRegistry().snapshot();
		expect(canonicalFile.schema).toBe(declared.schema);
		const materialized = canonicalFile.validators as Array<Record<string, unknown>>;
		expect(materialized.map((descriptor) => descriptor.id)).toEqual(
			declared.validators.map((descriptor) => descriptor.id),
		);
		for (const descriptor of materialized) {
			if (typeof descriptor.resource_path !== "string") continue;
			const content = readFileSync(join(repositoryRoot, "sure/canonical/skills", descriptor.resource_path));
			expect(descriptor.resource_digest).toBe(`sha256:${createHash("sha256").update(content).digest("hex")}`);
		}
		for (const skill of CANONICAL_SKILLS) {
			const pi = readJson(
				join(repositoryRoot, "sure/generated/pi/skills", skill.skill_id, "validator-registry.json"),
			);
			const portable = readJson(
				join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "validator-registry.json"),
			);
			expect(pi).toEqual(portable);
			expect(pi.schema).toBe("sure.validator.registry.v1");
			expect(pi.digest).toBe(canonicalFile.digest);
		}
	});

	it("associates auxiliary validators with their owning workflow unit", () => {
		const transDescriptors = canonicalValidatorRegistry()
			.snapshot()
			.validators.filter(
				(descriptor) =>
					descriptor.skill_id === "sure_trans" &&
					descriptor.branch_id === "main" &&
					descriptor.unit_id === "generate_adapter",
			);
		expect(transDescriptors.map((descriptor) => descriptor.legacy_id)).toEqual([
			"python-script",
			"legacy-in-process",
		]);
		expect(transDescriptors.map((descriptor) => descriptor.id)).toEqual([
			"sure.sure_trans.main.generate_adapter",
			"sure.sure_trans.main.generate_adapter_aux_legacy_in_process",
		]);
		expect(
			transDescriptors.every(
				(descriptor) => descriptor.input_contract === "workflow:sure_trans/main/generate_adapter",
			),
		).toBe(true);
	});

	it("binds registered gate validators to semantic operations without filename inference", () => {
		const descriptors = canonicalValidatorRegistry().snapshot().validators;
		const evalOperations = Object.fromEntries(
			descriptors
				.filter((descriptor) => descriptor.skill_id === "sure_eval" && descriptor.branch_id === "main")
				.map((descriptor) => [descriptor.unit_id, descriptor.backend_operation_id]),
		);
		expect(evalOperations).toMatchObject({
			execute_evaluation: "sure.eval.validate_eval_report",
			assessment: "sure.eval.validate_assessment",
			extract_lessons: "sure.memory.validate_extraction",
			run_report: "sure.eval.validate_run_report",
		});
		const inferOperations = Object.fromEntries(
			descriptors
				.filter((descriptor) => descriptor.skill_id === "sure_infer" && descriptor.branch_id === "main")
				.map((descriptor) => [descriptor.unit_id, descriptor.backend_operation_id]),
		);
		expect(inferOperations).toMatchObject({
			execute_inference: "sure.infer.validate_execution_result",
			extract_lessons: "sure.memory.validate_extraction",
			run_report: "sure.eval.validate_run_report",
		});
		const extractionOperations = Object.fromEntries(
			descriptors
				.filter((descriptor) => descriptor.unit_id === "extract_lessons")
				.map((descriptor) => [descriptor.skill_id, descriptor.backend_operation_id]),
		);
		expect(extractionOperations).toEqual({
			sure_eval: "sure.memory.validate_extraction",
			sure_feed: "sure.memory.validate_extraction",
			sure_infer: "sure.memory.validate_extraction",
			sure_onboard: "sure.memory.validate_extraction",
			sure_trans: "sure.memory.validate_extraction",
		});
		const feedOperations = Object.fromEntries(
			descriptors
				.filter((descriptor) => descriptor.skill_id === "sure_feed" && descriptor.branch_id === "main")
				.map((descriptor) => [descriptor.unit_id, descriptor.backend_operation_id]),
		);
		expect(feedOperations).toMatchObject({
			match_task: "sure.feed.validate_match_task",
			synthesize_model_input: "sure.feed.validate_model_input",
			rank_and_select: "sure.feed.validate_rank_select",
			extract_lessons: "sure.memory.validate_extraction",
		});
		const onboardOperations = Object.fromEntries(
			descriptors
				.filter((descriptor) => descriptor.skill_id === "sure_onboard" && descriptor.branch_id === "main")
				.map((descriptor) => [descriptor.unit_id, descriptor.backend_operation_id]),
		);
		expect(onboardOperations).toMatchObject({
			build_plan: "sure.onboard.validate_build_plan",
			validate_spec: "sure.onboard.validate_spec",
			prepare_fixture: "sure.onboard.validate_fixture",
			fetch_weights: "sure.onboard.validate_weights",
			save_artifacts: "sure.onboard.validate_artifact_manifest",
			extract_lessons: "sure.memory.validate_extraction",
		});
	});

	it("projects the legacy Pi manifest without changing its public fields", () => {
		for (const skill of CANONICAL_SKILLS) {
			const generated = readJson(
				join(repositoryRoot, "sure/generated/pi/skills", skill.skill_id, "sure.skill.json"),
			);
			const legacy = readJson(join(repositoryRoot, "sure/skills", skill.skill_id, "sure.skill.json"));
			expect(canonicalJson(asJson(generated))).toBe(canonicalJson(asJson(legacy)));
		}
	});

	it("keeps portable packages host-neutral and frontmatter-addressable", () => {
		const forbidden =
			/@earendil-works\/pi-coding-agent|HARNESS_PYTHON_BIN|sure_finish|sure_update_state|pre_start|pre_tool_call|post_tool_result|\.\.\/sure_[a-z_]+|sure\/skills\/sure_/;
		for (const skill of CANONICAL_SKILLS) {
			const root = join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug);
			const markdown = readFileSync(join(root, "SKILL.md"), "utf8");
			expect(markdown).toMatch(/^---\nname: [a-z0-9-]+\ndescription: /);
			expect(markdown).not.toMatch(forbidden);
			expect(readFileSync(join(root, "agents/openai.yaml"), "utf8")).toContain(`$${skill.distribution_slug}`);
			expect(readFileSync(join(root, "canonical-definition.json"), "utf8")).not.toMatch(forbidden);
		}
	});

	it("ships one memory contract projection to both hosts", () => {
		const canonical = readJson(join(repositoryRoot, "sure/canonical/shared/memory-contract.json"));
		expect(canonical).toEqual(CANONICAL_MEMORY_CONTRACT);
		for (const skill of CANONICAL_SKILLS) {
			const pi = readJson(join(repositoryRoot, "sure/generated/pi/skills", skill.skill_id, "memory-contract.json"));
			const portable = readJson(
				join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "memory-contract.json"),
			);
			expect(pi).toEqual(portable);
			const { skill: projection, ...base } = pi;
			expect(base).toEqual(canonical);
			expect(projection).toEqual({
				skill_id: skill.skill_id,
				enabled: skill.skill_id !== "sure_approve",
				participates_in_checkpoint: skill.skill_id !== "sure_approve",
			});
			const piLock = readJson(
				join(repositoryRoot, "sure/generated/pi/skills", skill.skill_id, "generation.lock.json"),
			);
			expect(piLock.memory_contract_digest).toBe(canonicalJsonDigest(asJson(pi)));
			expect(
				readFileSync(
					join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "memory-contract.json"),
					"utf8",
				),
			).not.toMatch(/@earendil-works\/pi-coding-agent|sure\/skills\/sure_/);
		}
	});

	it("ships a Pi-free portable memory entrypoint description", () => {
		for (const skill of CANONICAL_SKILLS) {
			const readme = readFileSync(
				join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "memory", "README.md"),
				"utf8",
			);
			expect(readme).toContain("surectl memory --contract ./memory-contract.json");
			expect(readme).toContain(`--skill ${skill.skill_id}`);
			expect(readme).not.toMatch(/@earendil-works\/pi-coding-agent|sure\/skills\/sure_|HARNESS_PYTHON_BIN/);
		}
	});

	it("projects one executor registry and digest to both hosts", () => {
		const canonical = executorRegistrySnapshot();
		const canonicalFile = readJson(join(repositoryRoot, "sure/canonical/shared/executor-registry.json"));
		expect(canonicalFile).toEqual(canonical);
		for (const skill of CANONICAL_SKILLS) {
			const pi = readJson(
				join(repositoryRoot, "sure/generated/pi/skills", skill.skill_id, "executor-registry.json"),
			);
			const portable = readJson(
				join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "executor-registry.json"),
			);
			expect(pi).toEqual(canonical);
			expect(portable).toEqual(canonical);
			const lock = readJson(
				join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "generation.lock.json"),
			);
			expect(lock.executor_registry_digest).toBe(canonical.registry_digest);
		}
	});

	it("ships one independently verifiable semantic runtime for both hosts", () => {
		const root = join(repositoryRoot, "sure/dist/portable-runtime");
		const workspace = mkdtempSync(join(tmpdir(), "sure-portable-workspace-"));
		onTestFinished(() => rmSync(workspace, { force: true, recursive: true }));
		const lock = readJson(join(root, "runtime-support.lock.json"));
		const { runtime_digest: runtimeDigest, ...unsigned } = lock;
		expect(lock.schema).toBe("sure.portable.runtime.lock.v1");
		expect(validateJsonSchema(readJson(join(root, "contracts/portable_runtime_lock.schema.json")), lock).ok).toBe(
			true,
		);
		expect(runtimeDigest).toBe(canonicalJsonDigest(asJson(unsigned)));
		const verified = verifyPortableRuntime(root, {
			expected_runtime_digest: String(runtimeDigest),
			expected_core_package_version: "0.80.3",
			expected_semantic_backend_registry_digest: String(lock.semantic_backend_registry_digest),
			expected_executor_registry_digest: String(lock.executor_registry_digest),
		});
		expect(verified.lock.runtime_digest).toBe(runtimeDigest);
		const files = lock.files as Array<{ path: string; size_bytes: number; sha256: string }>;
		expect(files.some((file) => file.path === ".sure-generated")).toBe(true);
		for (const file of files) {
			expect(file.path.startsWith("/")).toBe(false);
			expect(file.path.split("/")).not.toContain("..");
			const content = readFileSync(join(root, file.path));
			expect(content.byteLength).toBe(file.size_bytes);
			expect(`sha256:${createHash("sha256").update(content).digest("hex")}`).toBe(file.sha256);
		}
		const manifest = readJson(join(root, "semantic-backends.json"));
		expect(lock.semantic_backend_registry_digest).toBe(manifest.registry_digest);
		const entrypoints: string[] = [];
		const operationIds: string[] = [];
		for (const bundle of manifest.bundles as Array<Record<string, unknown>>) {
			for (const operation of bundle.operations as Array<Record<string, unknown>>) {
				const resolved = resolveSemanticBackendOperation(root, String(operation.operation_id), {
					manifestPath: join(root, "semantic-backends.json"),
					environment: {},
				});
				expect(resolved.source).toBe("package");
				expect(resolved.registry_digest).toBe(manifest.registry_digest);
				expect(resolved.bundle_digest).toBe(bundle.canonical_tree_digest);
				entrypoints.push(resolved.path);
				operationIds.push(String(operation.operation_id));
			}
		}
		expect(lock.operation_ids).toEqual(operationIds.sort());
		const pythonProbe = [
			"import json, sys",
			"from pathlib import Path",
			"from sure.runtime.semantic_backend import resolve_semantic_backend_operation",
			"root = Path(sys.argv[1]).resolve()",
			"manifest_path = root / 'semantic-backends.json'",
			"manifest = json.loads(manifest_path.read_text(encoding='utf-8'))",
			"operations = [op['operation_id'] for bundle in manifest['bundles'] for op in bundle['operations']]",
			"resolved = [resolve_semantic_backend_operation(op, package_dir=root, manifest_path=manifest_path, environment={}) for op in operations]",
			"assert all(item.source == 'package' for item in resolved)",
			"assert all(item.registry_digest == manifest['registry_digest'] for item in resolved)",
			"sys.path.insert(0, str(root / 'backends' / 'sure-evaluation-backend' / 'scripts'))",
			"import evaluation_runtime",
			"workspace = Path(sys.argv[2]).resolve()",
			"assert evaluation_runtime.REPO_ROOT == workspace",
			"assert evaluation_runtime.RUNTIME_SUPPORT_ROOT == root",
			"assert evaluation_runtime.SPEC_ROOT == root / 'sure' / 'runtime' / 'evaluation'",
			"assert evaluation_runtime.CACHE_ROOT == workspace / 'sure' / '.runtime' / 'evaluation'",
			"print(len(resolved))",
		].join("\n");
		const python = process.env.PYTHON?.trim() || "python3";
		const probeEnvironment = {
			...process.env,
			PYTHONDONTWRITEBYTECODE: "1",
			PYTHONPATH: root,
			SURE_REPOSITORY_ROOT: workspace,
			SURE_RUNTIME_SUPPORT_ROOT: root,
			SURE_SEMANTIC_BACKEND_MANIFEST: join(root, "semantic-backends.json"),
			SURE_SEMANTIC_BACKEND_ROOT: join(root, "backends"),
		};
		const probeOutput = execFileSync(python, ["-B", "-c", pythonProbe, root, workspace], {
			cwd: root,
			env: probeEnvironment,
			stdio: "pipe",
		}).toString("utf8");
		expect(Number(probeOutput.trim())).toBe(
			(manifest.bundles as Array<Record<string, unknown>>).reduce(
				(count, bundle) => count + (bundle.operations as unknown[]).length,
				0,
			),
		);
		for (const entrypoint of entrypoints) {
			execFileSync(python, ["-B", entrypoint, "--help"], {
				cwd: root,
				env: probeEnvironment,
				stdio: "pipe",
				timeout: 15_000,
			});
		}
		expect(() =>
			execFileSync(python, ["-B", entrypoints[0], "--help"], {
				cwd: root,
				env: { ...probeEnvironment, SURE_RUNTIME_SUPPORT_ROOT: workspace },
				stdio: "pipe",
				timeout: 15_000,
			}),
		).toThrow();
		expect(readdirSync(workspace)).toEqual([]);
		const combined = Buffer.concat(files.map((file) => readFileSync(join(root, file.path))));
		expect(combined.toString("utf8")).not.toMatch(/@earendil-works\/pi-coding-agent|\/hpc_stor03/);
		for (const skill of CANONICAL_SKILLS) {
			for (const packageRoot of [
				join(repositoryRoot, "sure/generated/pi/skills", skill.skill_id),
				join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug),
			]) {
				const backendProjection = readJson(join(packageRoot, "semantic-backends.json"));
				expect(backendProjection.runtime_distribution_digest).toBe(runtimeDigest);
			}
		}
	});

	it("is reproducible and does not require a production reference root", () => {
		const script = join(repositoryRoot, "scripts/generate-sure-skills.ts");
		execFileSync(process.execPath, ["--import", "tsx", script, "--check"], { cwd: repositoryRoot, stdio: "pipe" });
		for (const skill of CANONICAL_SKILLS) {
			expect(
				existsSync(join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "generation.lock.json")),
			).toBe(true);
		}
		expect(readFileSync(join(repositoryRoot, "sure/dist/agent-skills/registry.json"), "utf8")).not.toContain(
			"/hpc_stor03",
		);
		expect(
			readFileSync(join(repositoryRoot, "sure/dist/portable-runtime/runtime-support.lock.json"), "utf8"),
		).not.toContain("/hpc_stor03");
	});
});
