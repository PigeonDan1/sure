import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fauxAssistantMessage } from "@earendil-works/pi-ai/compat";
import { afterEach, describe, expect, it } from "vitest";
import { emitSessionShutdownEvent } from "../../src/core/extensions/runner.ts";
import { sureExtension } from "../../src/core/sure/index.ts";
import { createHarness, getUserTexts, type Harness } from "./harness.ts";

describe("Sure continuation before the terminal unit", () => {
	const cleanups: Array<() => void> = [];

	afterEach(() => {
		while (cleanups.length > 0) {
			cleanups.pop()?.();
		}
	});

	it("automatically closes a run whose first unit remains unfinished", async () => {
		const harness: Harness = await createHarness({ extensionFactories: [sureExtension], settings: {} });
		cleanups.push(harness.cleanup);
		const skillDir = join(harness.tempDir, ".sure", "skills", "demo");
		mkdirSync(join(skillDir, "skill"), { recursive: true });
		writeFileSync(join(skillDir, "skill", "SKILL.md"), "Produce the current unit's artifact before finishing.");
		writeFileSync(
			join(skillDir, "sure.skill.json"),
			JSON.stringify({ name: "demo", command: "sure_feed", prompt: "skill/SKILL.md", description: "Demo" }),
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
		harness.setResponses([
			() => {
				const runId = readdirSync(join(harness.tempDir, ".sure", "runs"))[0];
				writeFileSync(
					join(harness.tempDir, ".sure", "runs", runId, "state.json"),
					JSON.stringify({
						phase: { id: "dataset_scope", status: "running" },
						checkpoint: {
							resumable: true,
							resume_hint: 'Start at unit "dataset_scope".',
							data: { currentUnit: "dataset_scope", completedUnits: [] },
						},
					}),
				);
				return fauxAssistantMessage("The run is at the terminal unit. Calling sure_finish.");
			},
			fauxAssistantMessage("Still unfinished"),
			fauxAssistantMessage("Still unfinished"),
			fauxAssistantMessage("Still unfinished"),
			fauxAssistantMessage("Should not be requested automatically"),
		]);

		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();

		expect(getUserTexts(harness)).toHaveLength(4);
		expect(harness.getPendingResponseCount()).toBe(1);
		const runId = readdirSync(join(harness.tempDir, ".sure", "runs"))[0];
		const run = JSON.parse(readFileSync(join(harness.tempDir, ".sure", "runs", runId, "run.json"), "utf-8"));
		expect(run.status).toBe("failed");
		expect(run.lastRepair).toContain("did not call sure_finish after 3 continuations");
		expect(JSON.parse(readFileSync(run.manifestPath, "utf-8"))).toMatchObject({
			status: "failed",
			validation: { gate: "system_failure" },
		});
	});

	it("restores the unfinished checkpoint after a stale provider error", async () => {
		const harness: Harness = await createHarness({ extensionFactories: [sureExtension], settings: {} });
		cleanups.push(harness.cleanup);
		const skillDir = join(harness.tempDir, ".sure", "skills", "demo");
		mkdirSync(join(skillDir, "skill"), { recursive: true });
		writeFileSync(join(skillDir, "skill", "SKILL.md"), "Produce the dataset decision artifact.");
		writeFileSync(
			join(skillDir, "sure.skill.json"),
			JSON.stringify({ name: "demo", command: "sure_feed", prompt: "skill/SKILL.md", description: "Demo" }),
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
		harness.setResponses([fauxAssistantMessage("Paused before dataset_scope")]);
		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();

		const runId = readdirSync(join(harness.tempDir, ".sure", "runs"))[0];
		const statePath = join(harness.tempDir, ".sure", "runs", runId, "state.json");
		writeFileSync(
			statePath,
			JSON.stringify({
				phase: { id: "error", status: "failed" },
				message: "OpenAI API error (401)",
				diagnostics: [{ severity: "warning", message: "Old tool error" }],
				checkpoint: {
					resumable: true,
					resume_hint: 'Start at unit "dataset_scope".',
					data: { currentUnit: "dataset_scope", completedUnits: [] },
				},
			}),
		);
		await emitSessionShutdownEvent(harness.session.extensionRunner, { type: "session_shutdown", reason: "quit" });
		harness.setResponses([fauxAssistantMessage("Resuming dataset_scope")]);
		await harness.session.prompt(`/sure_resume ${runId}`);
		await harness.session.agent.waitForIdle();

		const state = JSON.parse(readFileSync(statePath, "utf-8"));
		expect(state.phase).toMatchObject({ id: "finish", status: "failed" });
		expect(state.checkpoint.data).toMatchObject({ currentUnit: "dataset_scope", completedUnits: [] });
		expect(state.diagnostics).toEqual([{ severity: "warning", message: "Old tool error" }]);
		expect(getUserTexts(harness).some((text) => text.includes('Resumed at unit "dataset_scope"'))).toBe(true);
		expect(getUserTexts(harness).every((text) => !text.includes("OpenAI API error"))).toBe(true);
	});

	it("runs an on_resume hook before prompting the agent", async () => {
		const harness: Harness = await createHarness({ extensionFactories: [sureExtension], settings: {} });
		cleanups.push(harness.cleanup);
		const skillDir = join(harness.tempDir, ".sure", "skills", "demo");
		mkdirSync(join(skillDir, "skill"), { recursive: true });
		mkdirSync(join(skillDir, "hooks"), { recursive: true });
		writeFileSync(join(skillDir, "skill", "SKILL.md"), "Complete the current unit.");
		writeFileSync(
			join(skillDir, "hooks", "index.ts"),
			'export function onResume() { return { ok: true, state_patch: { phase: { id: "execute_evaluation", status: "running" }, message: "Scope confirmed by resume hook", checkpoint: { resumable: true, data: { currentUnit: "execute_evaluation", completedUnits: ["dataset_scope"] } } } }; }',
		);
		writeFileSync(
			join(skillDir, "sure.skill.json"),
			JSON.stringify({
				name: "demo",
				command: "sure_feed",
				prompt: "skill/SKILL.md",
				hooks: { on_resume: [{ module: "hooks/index.ts", handler: "onResume" }] },
			}),
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
		harness.setResponses([fauxAssistantMessage("Paused")]);
		await harness.session.prompt("/sure_feed topic");
		await harness.session.agent.waitForIdle();
		const runId = readdirSync(join(harness.tempDir, ".sure", "runs"))[0];
		const statePath = join(harness.tempDir, ".sure", "runs", runId, "state.json");
		writeFileSync(
			statePath,
			JSON.stringify({
				checkpoint: { resumable: true, data: { currentUnit: "dataset_scope", completedUnits: [] } },
			}),
		);
		await emitSessionShutdownEvent(harness.session.extensionRunner, { type: "session_shutdown", reason: "quit" });
		harness.setResponses([fauxAssistantMessage("Continuing")]);
		await harness.session.prompt(`/sure_resume ${runId}`);
		await harness.session.agent.waitForIdle();

		const state = JSON.parse(readFileSync(statePath, "utf-8"));
		expect(state.checkpoint.data).toMatchObject({
			currentUnit: "execute_evaluation",
			completedUnits: ["dataset_scope"],
		});
		expect(getUserTexts(harness).some((text) => text.includes("Scope confirmed by resume hook"))).toBe(true);
	});
});
