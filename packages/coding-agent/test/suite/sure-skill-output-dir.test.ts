import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { preStart as evalPreStart } from "../../../../sure/skills/sure_eval/hooks/index.ts";
import { preStart } from "../../../../sure/skills/sure_infer/hooks/index.ts";
import type { SureHookContext } from "../../src/core/sure/types.ts";

const INFER_PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_infer");
const EVAL_PACKAGE_DIR = resolve(__dirname, "../../../../sure/skills/sure_eval");

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

function inferPreStart(name: string, args: string) {
	const ctx = inferCtx(name, args);
	const root = resolve(ctx.cwd, "..");
	const policyPath = join(root, "site.local.yaml");
	writeFileSync(
		policyPath,
		JSON.stringify({
			schema: "sure.site.policy.v1",
			site_id: "skill-output-dir-test",
			policy_version: 1,
			storage: {
				approved_models_roots: [join(root, "models")],
				approved_results_roots: [join(root, "results")],
				forbidden_output_roots: [join(root, "production-reference")],
				runtime_root: join(root, "runtime"),
			},
			datasets: { allowed_source_roots: { default: join(root, "datasets") } },
			execution: { surfaces: ["local"], local_runtimes: ["python"] },
		}),
	);
	const previousPolicy = process.env.SURE_SITE_POLICY;
	process.env.SURE_SITE_POLICY = policyPath;
	try {
		return preStart(ctx);
	} finally {
		if (previousPolicy === undefined) {
			delete process.env.SURE_SITE_POLICY;
		} else {
			process.env.SURE_SITE_POLICY = previousPolicy;
		}
	}
}

function evalCtx(name: string, args: string): SureHookContext {
	return preStartCtx("sure_eval", EVAL_PACKAGE_DIR, name, args);
}

describe("/sure_infer output_dir", () => {
	it("accepts output_dir instead of refusing it upfront", () => {
		const result = inferPreStart("accepts", "model=demo datasets=/ds/demo output_dir=/tmp/products");

		expect(result.repair ?? "").not.toContain("no longer accepts output_dir");
	});

	it("still refuses model_dir", () => {
		const result = inferPreStart("refuses-model-dir", "model=demo datasets=/ds/demo model_dir=/tmp/models");

		expect(result.ok).toBe(false);
		expect(result.repair ?? "").toContain("does not accept model_dir");
	});
});

// The parameter checks below run before any site policy or python spawn, so
// the verdicts are stable on every host; only "accepts" cases may fail later
// on a missing Harness Runtime, and those assert on the message alone.
describe("/sure_eval output_dir", () => {
	const VALID = "model=demo datasets=ds__v1 pipeline_id=p1";

	it("accepts output_dir so callers can collect the run result", () => {
		const result = evalPreStart(evalCtx("eval-accepts", `${VALID} output_dir=/tmp/products`));

		expect(result.repair ?? "").not.toContain("does not accept output_dir");
	});

	it("accepts source= as the prediction source", () => {
		const result = evalPreStart(evalCtx("eval-accepts-source", `${VALID} source=/tmp/other`));

		expect(result.repair ?? "").not.toContain("does not accept source");
	});

	it.each([
		"reuse_predictions_from",
		"model_dir",
		"tmp_root",
		"copy_mode",
		"max_samples",
		"config",
		"evaluation_engine_root",
	])("still refuses %s, which would change what is re-evaluated", (name) => {
		const result = evalPreStart(evalCtx(`eval-refuses-${name}`, `${VALID} ${name}=/tmp/other`));

		expect(result.ok).toBe(false);
		expect(result.repair ?? "").toContain(`does not accept ${name}`);
	});

	it("rejects pipeline_id together with metrics", () => {
		const result = evalPreStart(evalCtx("eval-both-selectors", `${VALID} metrics=wer`));

		expect(result.ok).toBe(false);
		expect(result.repair ?? "").toContain("exactly one of");
	});
});
