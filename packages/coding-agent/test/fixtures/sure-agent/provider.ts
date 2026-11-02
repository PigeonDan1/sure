import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fauxAssistantMessage, fauxToolCall, registerFauxProvider } from "@earendil-works/pi-ai/compat";
import type { ExtensionAPI } from "../../../src/core/extensions/index.ts";
import type { SureRunRecord } from "../../../src/core/sure/types.ts";

function activeRun(): SureRunRecord {
	const runs = join(process.cwd(), ".sure", "runs");
	for (const id of readdirSync(runs)) {
		const run = JSON.parse(readFileSync(join(runs, id, "run.json"), "utf8")) as SureRunRecord;
		if (run.status === "running") return run;
	}
	throw new Error("The fixture provider requires a running native SURE workflow.");
}

export default function fixtureProvider(pi: ExtensionAPI): void {
	const faux = registerFauxProvider({ provider: "sure-local-fixture", models: [{ id: "fixture-model" }] });
	const model = faux.getModel();
	pi.registerProvider("sure-local-fixture", {
		api: faux.api,
		apiKey: "fixture-only-no-network",
		baseUrl: model.baseUrl,
		models: faux.models.map((entry) => ({ ...entry })),
	});
	faux.setResponses([
		() => {
			const run = activeRun();
			writeFileSync(
				join(run.runDir, "manifest.json"),
				JSON.stringify({
					schema_version: "sure.fixture.manifest.v1",
					run_id: run.runId,
					skill_name: run.skillName,
					status: "success",
					created_at: new Date().toISOString(),
					inputs: { fixture: true },
					outputs: { proof: join(run.runDir, "artifacts", "proof.json") },
					validation: { gate: "fixture" },
				}),
			);
			return fauxAssistantMessage(
				fauxToolCall("sure_finish", {
					status: "success",
					manifest_path: join(run.runDir, "manifest.json"),
					summary: "must be rejected",
				}),
			);
		},
		() => {
			const run = activeRun();
			return fauxAssistantMessage(
				fauxToolCall("write", {
					path: join(run.runDir, "artifacts", "proof.json"),
					content: JSON.stringify({ fixture: "native-gate" }),
				}),
			);
		},
		() => {
			const run = activeRun();
			return fauxAssistantMessage(
				fauxToolCall("sure_finish", {
					status: "success",
					manifest_path: join(run.runDir, "manifest.json"),
					summary: "offline fixture completed",
				}),
			);
		},
	]);
}
