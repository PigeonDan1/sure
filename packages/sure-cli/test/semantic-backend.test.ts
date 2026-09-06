import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
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
			writeFileSync(join(backendRoot, "sure-evaluation-backend", "unregistered.txt"), "tamper\n");
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
			const unrelated = join(canonicalRoot, "sure-infer", "tampered.txt");
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
