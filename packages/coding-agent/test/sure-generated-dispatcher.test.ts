import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import type { CapabilityRequirement, ExecutionRequest } from "@earendil-works/sure-core";
import { afterEach, describe, expect, it } from "vitest";
import {
	createPiGeneratedLocalRequestDispatcherResolver,
	type PiGeneratedDispatcherOptions,
} from "../src/core/sure/generated-dispatcher.ts";
import type { SureHookContext } from "../src/core/sure/types.ts";

const REPOSITORY_ROOT = resolve(import.meta.dirname, "../../..");
const GENERATED_PACKAGE = join(REPOSITORY_ROOT, "sure", "generated", "pi", "skills", "sure_onboard");
const roots: string[] = [];
const DIGEST = `sha256:${"a".repeat(64)}`;

function context(): Omit<SureHookContext, "point"> {
	const root = mkdtempSync(join(tmpdir(), "sure-generated-dispatcher-"));
	roots.push(root);
	const packageDir = join(root, "package");
	const runDir = join(root, "run");
	cpSync(GENERATED_PACKAGE, packageDir, { recursive: true });
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	return {
		run: { runId: "generated-dispatcher", runDir, outputDir: runDir } as never,
		skill: { name: "sure_onboard", command: "/sure_onboard" } as never,
		cwd: REPOSITORY_ROOT,
		packageDir,
		runDir,
		args: "",
		repoRoot: REPOSITORY_ROOT,
	};
}

function request(ctx: Omit<SureHookContext, "point">, operationId = "sure.onboard.execute_import"): ExecutionRequest {
	const outputPath = join(ctx.runDir, "artifacts", "import_result.json");
	return {
		runtime_requirements: { semantic_backend_operation_id: operationId },
		entrypoint: {
			executable: process.execPath,
			argv: [join(ctx.packageDir, "scripts", "run_validate.py"), "--run-dir", ctx.runDir, "--produces", outputPath],
			working_directory: ctx.packageDir,
		},
	} as unknown as ExecutionRequest;
}

function options(ctx: Omit<SureHookContext, "point">): PiGeneratedDispatcherOptions {
	return {
		operation_ids: ["sure.onboard.execute_import"],
		resolveRuntime: () => ({
			ok: true,
			contract: {
				runtime_id: "generated-test-runtime",
				executable: process.execPath,
				details: {
					lock_sha256: DIGEST,
					manifest_path: join(ctx.packageDir, "runtime-manifest.json"),
					runtime_root: ctx.packageDir,
				},
			},
		}),
		executeBackend: ({ args }) => {
			const producesIndex = args.indexOf("--produces");
			const outputPath = args[producesIndex + 1];
			if (producesIndex < 0 || outputPath === undefined) {
				return { ok: false, stdout: "", stderr: "missing output", status: null };
			}
			writeFileSync(outputPath, '{"ok":true}\n', "utf8");
			return { ok: true, stdout: "generated", stderr: "", status: 0 };
		},
	};
}

const requirements: CapabilityRequirement[] = [
	{
		capability_id: "sure.execution.harness-python",
		capability_class: "execution_capability",
		required: true,
	},
];

afterEach(() => {
	for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("verified generated Pi dispatcher resolver", () => {
	it("binds only the explicitly selected generated operation", () => {
		const ctx = context();
		const resolver = createPiGeneratedLocalRequestDispatcherResolver(ctx, options(ctx));
		const selected = resolver(request(ctx));
		expect(selected).toBeDefined();
		if (selected === undefined) throw new Error("expected generated dispatcher");
		expect(selected.probe(request(ctx), requirements)[0]).toMatchObject({
			capability_id: "sure.execution.harness-python",
			status: "AVAILABLE",
			source: "host_probe",
		});
		expect(selected.execute(request(ctx))).toMatchObject({ ok: true, status: 0, stdout: "generated" });
		expect(resolver(request(ctx, "sure.onboard.execute_load"))).toBeUndefined();
	});

	it("reports a missing capability when no host runtime adapter is configured", () => {
		const ctx = context();
		const resolver = createPiGeneratedLocalRequestDispatcherResolver(ctx);
		const selected = resolver(request(ctx));
		if (selected === undefined) throw new Error("expected generated dispatcher");
		expect(selected.probe(request(ctx), requirements)[0]).toMatchObject({
			status: "MISSING",
			source: "host_probe",
		});
		expect(selected.execute(request(ctx))).toMatchObject({
			ok: false,
			status: null,
			stderr: "generated dispatcher runtime adapter is not configured",
		});
	});

	it("rejects a generated registry or script drift before creating a dispatcher", () => {
		const registryDrift = context();
		const lockPath = join(registryDrift.packageDir, "generation.lock.json");
		const lock = JSON.parse(readFileSync(lockPath, "utf8")) as Record<string, unknown>;
		lock.semantic_backend_registry_digest = DIGEST;
		writeFileSync(lockPath, `${JSON.stringify(lock)}\n`, "utf8");
		expect(() => createPiGeneratedLocalRequestDispatcherResolver(registryDrift, options(registryDrift))).toThrow(
			/semantic backend registry does not match/,
		);

		const scriptDrift = context();
		const scriptPath = join(scriptDrift.packageDir, "scripts", "run_validate.py");
		writeFileSync(scriptPath, `${readFileSync(scriptPath, "utf8")}\n# drift\n`, "utf8");
		expect(() => createPiGeneratedLocalRequestDispatcherResolver(scriptDrift, options(scriptDrift))).toThrow(
			/entrypoint digest mismatch/,
		);
	});

	it("rechecks package integrity after resolver creation", () => {
		const ctx = context();
		const resolver = createPiGeneratedLocalRequestDispatcherResolver(ctx, options(ctx));
		const scriptPath = join(ctx.packageDir, "scripts", "run_validate.py");
		writeFileSync(scriptPath, `${readFileSync(scriptPath, "utf8")}\n# post-bind drift\n`, "utf8");
		const selected = resolver(request(ctx));
		if (selected === undefined) throw new Error("expected generated dispatcher");
		expect(() => selected.probe(request(ctx), requirements)).toThrow(/entrypoint digest mismatch/);
	});
});
