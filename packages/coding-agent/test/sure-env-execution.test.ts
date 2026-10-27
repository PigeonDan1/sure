import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { preToolCall } from "../../../sure/skills/sure_onboard/hooks/index.ts";
import type { SureHookContext } from "../src/core/sure/types.ts";

const root = resolve(__dirname, "../../..");
const packageDir = join(root, "sure/skills/sure_onboard");
const temporary: string[] = [];

afterEach(() => {
	for (const path of temporary.splice(0)) rmSync(path, { recursive: true, force: true });
});

describe("M3 environment helper", () => {
	it.each([
		[
			"build_env",
			'"$HARNESS_PYTHON_BIN" scripts/run_env_command.py start --command "uv pip sync --python .venv/bin/python requirements.lock"',
			true,
		],
		["build_plan", '"$HARNESS_PYTHON_BIN" scripts/run_env_command.py start --command pwd', false],
		[
			"build_env",
			'"$HARNESS_PYTHON_BIN" scripts/run_env_command.py start --command "python scripts/check_weights.py"',
			false,
		],
		[
			"build_env",
			'"$HARNESS_PYTHON_BIN" scripts/run_env_command.py start --command "\\"$HARNESS_PYTHON_BIN\\" scripts/materialize_model_runtime.py"',
			true,
		],
		["build_env", "/model/.venv/bin/python scripts/run_env_command.py start --command pwd", false],
		[
			"build_env",
			'echo --command x; /model/.venv/bin/python scripts/run_env_command.py start --command "pwd"',
			false,
		],
		[
			"build_env",
			'"$HARNESS_PYTHON_BIN" scripts/run_env_command.py start --command "pwd"; /model/.venv/bin/python scripts/run_env_command.py status',
			false,
		],
		[
			"build_env",
			'"$HARNESS_PYTHON_BIN" scripts/run_env_command.py start --command "/model/.venv/bin/python scripts/run_env_command.py status"',
			false,
		],
		[
			"build_env",
			'"$HARNESS_PYTHON_BIN" scripts/run_env_command.py start --command "/model/.venv/bin/python scripts/materialize_model_runtime.py"',
			false,
		],
		["build_env", "nohup uv pip install -r requirements.txt > install.log 2>&1 &", false],
		["build_env", "nohup tail -f artifacts/build.log > /dev/null 2>&1 &", true],
		["build_env", "uv pip install -r requirements.txt", true],
		["fetch_weights", "nohup uv pip install -r requirements.txt > install.log 2>&1 &", true],
		[
			"build_env",
			'"$HARNESS_PYTHON_BIN" scripts/run_env_command.py start --command "setsid uv pip sync --python .venv/bin/python requirements.lock"',
			false,
		],
	])("keeps script ownership and interpreter checks: %s / %s", (unit, command, allowed) => {
		const runDir = mkdtempSync(join(tmpdir(), "m3-hook-"));
		temporary.push(runDir);
		writeFileSync(
			join(runDir, "state.json"),
			JSON.stringify({
				checkpoint: { data: { currentUnit: unit, completedUnits: [], retries: {} } },
			}),
		);
		const ctx = {
			point: "pre_tool_call",
			cwd: packageDir,
			packageDir,
			runDir,
			args: "",
			event: { toolName: "bash", input: { command } },
		} as SureHookContext;
		expect(preToolCall(ctx).ok).toBe(allowed);
	});

	// U+E carries the stage rules through the reference playbook instead of
	// per-stage prompts: the same rules must be present in env_uv.md, and the
	// fetch-weights long-download guidance must live in the checkpoint rule.
	it("supplies selection and execution rules through the reference playbook", () => {
		const playbook = readFileSync(join(packageDir, "references/playbooks/env_uv.md"), "utf8");
		for (const rule of ["--uv-cache-dir", "--uv-config", "状态不明", "查询等待", "不新增", "复用"])
			expect(playbook).toContain(rule);
		expect(playbook).toContain("nohup/setsid");
		expect(playbook).not.toMatch(/python3\.10|torch==|unsafe-best-match.*默认|一律放后台/);
		const contract = readFileSync(join(packageDir, "references/contracts/model_local_checkpoint_rule.md"), "utf8");
		expect(contract).toContain("## Long Downloads");
		expect(contract).toContain("exit-code file");
	});
});
