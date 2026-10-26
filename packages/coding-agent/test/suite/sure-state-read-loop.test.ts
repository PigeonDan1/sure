import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fauxAssistantMessage, fauxToolCall } from "@earendil-works/pi-ai/compat";
import { afterEach, describe, expect, it } from "vitest";
import { sureExtension } from "../../src/core/sure/index.ts";
import { createHarness, type Harness } from "./harness.ts";

describe("Sure unchanged state read loop", () => {
	const cleanups: Array<() => void> = [];

	afterEach(() => {
		while (cleanups.length > 0) cleanups.pop()?.();
	});

	async function setupHarness(): Promise<Harness> {
		const harness: Harness = await createHarness({ extensionFactories: [sureExtension], settings: {} });
		cleanups.push(harness.cleanup);
		const skillDir = join(harness.tempDir, ".sure", "skills", "demo");
		mkdirSync(join(skillDir, "skill"), { recursive: true });
		writeFileSync(join(skillDir, "skill", "SKILL.md"), "Complete the current unit.");
		writeFileSync(
			join(skillDir, "sure.skill.json"),
			JSON.stringify({ name: "demo", command: "sure_feed", prompt: "skill/SKILL.md" }),
		);
		await harness.session.bindExtensions({
			mode: "tui",
			onError: (error) => {
				throw new Error(JSON.stringify(error));
			},
			commandContextActions: {
				waitForIdle: () => harness.session.agent.waitForIdle(),
				newSession: async () => ({ cancelled: false }),
				fork: async () => ({ cancelled: false }),
				navigateTree: async () => ({ cancelled: false }),
				switchSession: async () => ({ cancelled: false }),
				reload: async () => {},
			},
		});
		return harness;
	}

	it("fails a run rather than reading the same checkpoint indefinitely", async () => {
		const harness = await setupHarness();
		harness.setResponses([
			() => {
				const runId = readdirSync(join(harness.tempDir, ".sure", "runs"))[0];
				writeFileSync(
					join(harness.tempDir, ".sure", "runs", runId, "state.json"),
					JSON.stringify({
						checkpoint: {
							resumable: true,
							data: { currentUnit: "execute_evaluation", completedUnits: ["dataset_scope"] },
						},
					}),
				);
				return fauxAssistantMessage(fauxToolCall("read", { path: `.sure/runs/${runId}/state.json`, offset: 1 }));
			},
			...Array.from({ length: 11 }, () => () => {
				const runId = readdirSync(join(harness.tempDir, ".sure", "runs"))[0];
				return fauxAssistantMessage(fauxToolCall("read", { path: `.sure/runs/${runId}/state.json`, offset: 1 }));
			}),
			fauxAssistantMessage("Should not be requested after the loop is stopped"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();

		const runId = readdirSync(join(harness.tempDir, ".sure", "runs"))[0];
		const run = JSON.parse(readFileSync(join(harness.tempDir, ".sure", "runs", runId, "run.json"), "utf-8"));
		expect(run.status).toBe("failed");
		expect(run.lastRepair).toContain("read the unchanged Sure state 12 times");
		expect(JSON.parse(readFileSync(run.manifestPath, "utf-8"))).toMatchObject({
			status: "failed",
			validation: { gate: "system_failure" },
		});
	});

	it("fails repeated identical bash errors before an endless tool loop", async () => {
		const harness = await setupHarness();
		harness.setResponses([
			() => {
				const runId = readdirSync(join(harness.tempDir, ".sure", "runs"))[0];
				writeFileSync(
					join(harness.tempDir, ".sure", "runs", runId, "state.json"),
					JSON.stringify({
						checkpoint: {
							resumable: true,
							data: { currentUnit: "run_report", completedUnits: ["execute_evaluation"] },
						},
					}),
				);
				return fauxAssistantMessage(fauxToolCall("bash", { command: "false" }));
			},
			...Array.from({ length: 5 }, () => fauxAssistantMessage(fauxToolCall("bash", { command: "false" }))),
			fauxAssistantMessage("Should not be requested after repeated failures"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();

		const runId = readdirSync(join(harness.tempDir, ".sure", "runs"))[0];
		const run = JSON.parse(readFileSync(join(harness.tempDir, ".sure", "runs", runId, "run.json"), "utf-8"));
		expect(run.status).toBe("failed");
		expect(run.lastRepair).toContain("same bash command failed 6 times");
		expect(JSON.parse(readFileSync(run.manifestPath, "utf-8"))).toMatchObject({
			status: "failed",
			validation: { gate: "system_failure" },
		});
	});
});
