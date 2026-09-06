import { createHash } from "node:crypto";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { canonicalJsonDigest } from "../../sure-core/src/contracts/canonical-json.ts";
import type { JsonValue } from "../../sure-core/src/contracts/types.ts";
import {
	loadSemanticBackendManifest,
	resolveSemanticBackendOperation,
	SemanticBackendResolutionError,
} from "../../sure-core/src/evaluation/index.ts";

const repositoryRoot = fileURLToPath(new URL("../../..", import.meta.url));
const manifestPath = join(repositoryRoot, "sure", "canonical", "shared", "evaluation", "backend-manifest.json");
const packageDir = join(repositoryRoot, "sure", "skills", "sure_eval");

describe("semantic backend registry", () => {
	it("resolves a pinned operation and exposes its immutable identity", () => {
		const manifest = loadSemanticBackendManifest(packageDir, { manifestPath });
		const resolved = resolveSemanticBackendOperation(packageDir, "sure.eval.run", {
			manifestPath,
			expectedRegistryDigest: manifest.registry_digest,
		});
		expect(resolved.source).toBe("canonical");
		expect(resolved.path).toBe(
			join(repositoryRoot, "sure", "canonical", "skills", "sure-infer", "scripts", "run_eval.py"),
		);
		expect(resolved.resource_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
		expect(resolved.bundle_digest).toBe(manifest.bundles[0].canonical_tree_digest);
		expect(resolved.integrity_root).toBe("scripts");
		expect(resolved.kind).toBe("execute");
		expect(resolved.consumer_skill_ids).toEqual(["sure_eval"]);
	});

	it("admits inference gates only through their registered operations", () => {
		const execution = resolveSemanticBackendOperation(packageDir, "sure.infer.validate_execution_result", {
			manifestPath,
		});
		expect(execution.kind).toBe("validate");
		expect(execution.consumer_skill_ids).toEqual(["sure_infer"]);
		expect(execution.path).toBe(
			join(repositoryRoot, "sure", "canonical", "skills", "sure-infer", "scripts", "check_execution_result.py"),
		);
		const report = resolveSemanticBackendOperation(packageDir, "sure.eval.validate_run_report", { manifestPath });
		expect(report.kind).toBe("validate");
		expect(report.consumer_skill_ids).toEqual(["sure_eval", "sure_infer"]);
	});

	it("resolves the shared memory gate outside every sibling skill", () => {
		const memory = resolveSemanticBackendOperation(packageDir, "sure.memory.validate_extraction", {
			manifestPath,
		});
		expect(memory.kind).toBe("validate");
		expect(memory.source).toBe("canonical");
		expect(memory.consumer_skill_ids).toEqual(["sure_feed", "sure_onboard", "sure_infer", "sure_eval", "sure_trans"]);
		expect(memory.path).toBe(
			join(repositoryRoot, "sure", "canonical", "shared", "memory-backend", "scripts", "check_memory_extraction.py"),
		);
	});

	it("uses an installed backend root only when its complete tree matches the manifest", () => {
		const temporary = mkdtempSync(join(tmpdir(), "sure-semantic-backend-"));
		try {
			const backendRoot = join(temporary, "backend");
			cpSync(
				join(repositoryRoot, "sure", "canonical", "skills", "sure-infer"),
				join(backendRoot, "sure-evaluation-backend"),
				{
					recursive: true,
				},
			);
			const resolved = resolveSemanticBackendOperation(packageDir, "sure.eval.run", {
				manifestPath,
				environment: { ...process.env, SURE_SEMANTIC_BACKEND_ROOT: backendRoot },
			});
			expect(resolved.source).toBe("semantic-backend-root");
			expect(resolved.path).toBe(join(backendRoot, "sure-evaluation-backend", "scripts", "run_eval.py"));
			writeFileSync(join(backendRoot, "sure-evaluation-backend", "unregistered.txt"), "host metadata\n");
			expect(
				resolveSemanticBackendOperation(packageDir, "sure.eval.run", {
					manifestPath,
					environment: { ...process.env, SURE_SEMANTIC_BACKEND_ROOT: backendRoot },
				}).source,
			).toBe("semantic-backend-root");
			writeFileSync(join(backendRoot, "sure-evaluation-backend", "scripts", "unregistered.txt"), "tamper\n");
			expect(() =>
				resolveSemanticBackendOperation(packageDir, "sure.eval.run", {
					manifestPath,
					environment: { ...process.env, SURE_SEMANTIC_BACKEND_ROOT: backendRoot },
				}),
			).toThrow(SemanticBackendResolutionError);
		} finally {
			rmSync(temporary, { recursive: true, force: true });
		}
	});

	it("rejects symlink substitution and digest mismatches", () => {
		const temporary = mkdtempSync(join(tmpdir(), "sure-semantic-backend-"));
		try {
			const canonicalRoot = join(temporary, "canonical");
			cpSync(join(repositoryRoot, "sure", "canonical", "skills", "sure-infer"), join(canonicalRoot, "sure-infer"), {
				recursive: true,
			});
			const unrelated = join(canonicalRoot, "sure-infer", "scripts", "tampered.txt");
			writeFileSync(unrelated, "tamper\n");
			const environment = { ...process.env, SURE_CANONICAL_SKILLS_ROOT: canonicalRoot };
			expect(() =>
				resolveSemanticBackendOperation(packageDir, "sure.eval.run", { manifestPath, environment }),
			).toThrow(SemanticBackendResolutionError);
			const target = join(canonicalRoot, "sure-infer", "scripts", "run_eval.py");
			unlinkSync(target);
			symlinkSync(
				join(repositoryRoot, "sure", "canonical", "skills", "sure-infer", "scripts", "run_eval.py"),
				target,
			);
			expect(() =>
				resolveSemanticBackendOperation(packageDir, "sure.eval.run", { manifestPath, environment }),
			).toThrow(SemanticBackendResolutionError);
		} finally {
			rmSync(temporary, { recursive: true, force: true });
		}
	});

	it("does not accept a wrong registry or bundle digest", () => {
		expect(() =>
			resolveSemanticBackendOperation(packageDir, "sure.eval.run", {
				manifestPath,
				expectedRegistryDigest: `sha256:${"0".repeat(64)}`,
			}),
		).toThrow(SemanticBackendResolutionError);
		expect(() =>
			resolveSemanticBackendOperation(packageDir, "sure.eval.run", {
				manifestPath,
				expectedBundleDigest: `sha256:${"f".repeat(64)}`,
			}),
		).toThrow(SemanticBackendResolutionError);
	});

	it("resolves explicitly repository-relative backend roots without weakening tree admission", () => {
		const temporary = mkdtempSync(join(tmpdir(), "sure-semantic-repository-root-"));
		try {
			const repository = join(temporary, "repository");
			const packageRoot = join(repository, "package");
			const backendRoot = join(repository, "sure", "runtime", "shared-validator");
			mkdirSync(packageRoot, { recursive: true });
			mkdirSync(backendRoot, { recursive: true });
			const script = Buffer.from("print('shared validator')\n", "utf8");
			writeFileSync(join(backendRoot, "check.py"), script);
			const resourceDigest = `sha256:${createHash("sha256").update(script).digest("hex")}`;
			const treeDigest = `sha256:${createHash("sha256").update(`check.py\0${resourceDigest}`).digest("hex")}`;
			const bundle = {
				schema: "sure.semantic.backend.bundle.v1",
				bundle_id: "shared-validator",
				version: "test-v1",
				description: "Repository-relative test backend.",
				canonical_root: "sure/runtime/shared-validator",
				canonical_root_kind: "repository",
				legacy_root: "sure/runtime/shared-validator",
				legacy_root_kind: "repository",
				integrity_root: ".",
				canonical_tree_digest: treeDigest,
				legacy_tree_digest: treeDigest,
				operations: [
					{
						operation_id: "sure.test.shared.validate",
						description: "Validate from a repository root.",
						entrypoint: "check.py",
						consumer_skill_ids: ["sure_infer"],
						kind: "validate",
						timeout_ms: 1_000,
						deterministic: true,
						canonical_resource_digest: resourceDigest,
						legacy_resource_digest: resourceDigest,
					},
				],
			};
			const unsigned = { schema: "sure.semantic.backend.manifest.v1", bundles: [bundle] };
			const syntheticManifest = join(temporary, "semantic-backends.json");
			writeFileSync(
				syntheticManifest,
				JSON.stringify({ ...unsigned, registry_digest: canonicalJsonDigest(unsigned as unknown as JsonValue) }),
			);
			const resolved = resolveSemanticBackendOperation(packageRoot, "sure.test.shared.validate", {
				manifestPath: syntheticManifest,
				environment: { SURE_REPOSITORY_ROOT: repository },
			});
			expect(resolved.source).toBe("canonical");
			expect(resolved.path).toBe(join(backendRoot, "check.py"));
			const invalidUnsigned = {
				...unsigned,
				bundles: [{ ...bundle, canonical_root_kind: "ambient" }],
			};
			const invalidManifest = join(temporary, "invalid-semantic-backends.json");
			writeFileSync(
				invalidManifest,
				JSON.stringify({
					...invalidUnsigned,
					registry_digest: canonicalJsonDigest(invalidUnsigned as unknown as JsonValue),
				}),
			);
			expect(() =>
				loadSemanticBackendManifest(packageRoot, {
					manifestPath: invalidManifest,
					environment: { SURE_REPOSITORY_ROOT: repository },
				}),
			).toThrow(SemanticBackendResolutionError);
			writeFileSync(join(backendRoot, "unregistered.txt"), "tamper\n");
			expect(() =>
				resolveSemanticBackendOperation(packageRoot, "sure.test.shared.validate", {
					manifestPath: syntheticManifest,
					environment: { SURE_REPOSITORY_ROOT: repository },
				}),
			).toThrow(SemanticBackendResolutionError);
		} finally {
			rmSync(temporary, { recursive: true, force: true });
		}
	});

	it("keeps the generated manifest digest stable across host projections", () => {
		const canonical = loadSemanticBackendManifest(packageDir, { manifestPath });
		const portable = loadSemanticBackendManifest(join(repositoryRoot, "sure", "dist", "agent-skills", "sure-eval"));
		expect(portable.registry_digest).toBe(canonical.registry_digest);
		expect(
			readFileSync(
				join(repositoryRoot, "sure", "dist", "agent-skills", "sure-eval", "semantic-backends.json"),
				"utf8",
			),
		).toContain(canonical.registry_digest);
	});
});
