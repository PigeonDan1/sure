import { mkdirSync, rmSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { preStart } from "../../../../sure/skills/sure_infer/hooks/index.ts";
import { preStart as revalPreStart } from "../../../../sure/skills/sure_reval/hooks/index.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

const INFER_PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_infer");
const RINFER_PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_reval");

function preStartCtx(skill: string, packageDir: string, name: string, args: string): SureHookContext {
	const root = resolve(__dirname, "tmp-eval-output-dir", name);
	rmSync(root, { recursive: true, force: true });
	const cwd = join(root, "cwd");
	const runDir = join(root, "run");
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	mkdirSync(cwd, { recursive: true });
	return {
		point: "pre_start",
		run: { id: `test-${name}`, command: `/${skill}`, status: "running" } as never,
		skill: { name: skill, command: `/${skill}` } as never,
		cwd,
		packageDir,
		runDir,
		args,
	};
}

function inferCtx(name: string, args: string): SureHookContext {
	return preStartCtx("sure_infer", INFER_PACKAGE_DIR, name, args);
}

function rinferCtx(name: string, args: string): SureHookContext {
	return preStartCtx("sure_reval", RINFER_PACKAGE_DIR, name, args);
}

describe("/sure_infer output_dir", () => {
	it("accepts output_dir instead of refusing it upfront", () => {
		const result = preStart(inferCtx("accepts", "model=demo datasets=/ds/demo output_dir=/tmp/products"));

		expect(result.repair ?? "").not.toContain("no longer accepts output_dir");
	});

	it("still refuses model_dir", () => {
		const result = preStart(inferCtx("refuses-model-dir", "model=demo datasets=/ds/demo model_dir=/tmp/models"));

		expect(result.ok).toBe(false);
		expect(result.repair ?? "").toContain("does not accept model_dir");
	});
});

describe("/sure_reval output_dir", () => {
	it("accepts output_dir so callers can collect the run result", () => {
		const result = revalPreStart(
			rinferCtx("reval-accepts", "model=demo datasets=ds__v1 pipeline_id=p1 output_dir=/tmp/products"),
		);

		expect(result.repair ?? "").not.toContain("no longer accepts output_dir");
	});

	it("still refuses the parameters that would change what is re-evaluated", () => {
		const result = revalPreStart(
			rinferCtx("reval-refuses-source", "model=demo datasets=ds__v1 pipeline_id=p1 source=/tmp/other"),
		);

		expect(result.ok).toBe(false);
		expect(result.repair ?? "").toContain("no longer accepts source");
	});
});
