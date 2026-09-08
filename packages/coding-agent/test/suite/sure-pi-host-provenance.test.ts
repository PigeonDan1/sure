import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { SureHookContext } from "@earendil-works/pi-coding-agent/hooks";
import {
	type CapabilityEvidence,
	type CapabilityRequirement,
	canonicalJsonDigest,
	type ExecutionProvenancePublisher,
	type ExecutionRequestDispatcher,
	type JsonValue,
	validateExecutionContractHistory,
} from "@earendil-works/sure-core";
import { afterEach, describe, expect, it } from "vitest";
import { runPiRegisteredOperation } from "../../../../sure/runtime/harness/registered-operation.ts";
import {
	createPiExecutionProvenanceHost,
	createPiExecutionProvenanceHostForContext,
	type PiExecutionProvenanceHost,
	type PiExecutionProvenanceSession,
} from "../../src/core/sure/execution-provenance.ts";

const REPOSITORY_ROOT = resolve(import.meta.dirname, "../../../..");
const ENTRYPOINT = join(
	REPOSITORY_ROOT,
	"sure",
	"canonical",
	"shared",
	"onboard-execution",
	"scripts",
	"run_validate.py",
);
const REGISTRY = join(REPOSITORY_ROOT, "sure", "canonical", "shared", "evaluation", "backend-manifest.json");
const GENERATED_PACKAGE = join(REPOSITORY_ROOT, "sure", "generated", "pi", "skills", "sure_onboard");
const TEMP_ROOT = join(import.meta.dirname, "tmp-pi-host-provenance");
const DIGEST = `sha256:${"a".repeat(64)}`;
const NOW = "2026-09-08T00:00:00.000Z";

interface HostFixture {
	ctx: SureHookContext;
	artifactPath: string;
	sessions: Array<Record<string, any>>;
	host: PiExecutionProvenanceHost;
}

function fixture(
	name: string,
	publisherFactory?: (session: Record<string, any>) => ExecutionProvenancePublisher,
	capabilityEvidenceFor?: (requirements: readonly CapabilityRequirement[]) => readonly CapabilityEvidence[],
	executionDispatcher?: ExecutionRequestDispatcher,
): HostFixture {
	const root = join(TEMP_ROOT, name);
	const packageDir = join(root, "sure_onboard");
	const runDir = join(root, "run");
	const artifactPath = join(runDir, "artifacts", "import_result.json");
	mkdirSync(join(packageDir, "scripts"), { recursive: true });
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	copyFileSync(ENTRYPOINT, join(packageDir, "scripts", "run_validate.py"));
	writeFileSync(artifactPath, '{"stage":"before"}\n', "utf8");
	const registry = JSON.parse(readFileSync(REGISTRY, "utf8")) as { registry_digest: string };
	writeFileSync(
		join(packageDir, "generation.lock.json"),
		`${JSON.stringify({
			schema: "sure.skill.generation.lock.v1",
			host: "pi",
			skill_id: "sure_onboard",
			semantic_backend_registry_digest: registry.registry_digest,
		})}\n`,
		"utf8",
	);
	let id = 0;
	const sessions: Array<Record<string, any>> = [];
	const base = createPiExecutionProvenanceHost({
		run: { runId: name, runDir, outputDir: runDir },
		package_dir: packageDir,
		skill_id: "sure_onboard",
		workflow_digest: DIGEST,
		validator_registry_digest: DIGEST,
		semantic_runtime_digest: DIGEST,
		semantic_backend_registry_digest: registry.registry_digest,
		executor_registry_digest: DIGEST,
		core_package_version: "0.80.3",
		reference_snapshot_digest: DIGEST,
		policy_digest: DIGEST,
		python_executable: process.execPath,
		now: () => NOW,
		new_id: () => `id-${++id}`,
		execution_dispatcher: executionDispatcher,
		capability_evidence_for: capabilityEvidenceFor,
	});
	const host: PiExecutionProvenanceHost = {
		issue(input) {
			const issued = base.issue(input) as unknown as Record<string, any>;
			const session = publisherFactory === undefined ? issued : { ...issued, publisher: publisherFactory(issued) };
			sessions.push(session);
			return session as unknown as PiExecutionProvenanceSession;
		},
	};
	const ctx: SureHookContext = {
		point: "post_tool_result",
		run: {
			runId: name,
			command: "/sure_onboard",
			status: "running",
			runDir,
			outputDir: runDir,
			workflowDigest: DIGEST,
			validatorDigest: DIGEST,
			executorDigest: DIGEST,
			coreVersion: "0.80.3",
		} as never,
		skill: { name: "sure_onboard", command: "/sure_onboard" } as never,
		cwd: REPOSITORY_ROOT,
		packageDir,
		runDir,
		args: "",
		repoRoot: REPOSITORY_ROOT,
		executionProvenance: host,
	};
	return { ctx, artifactPath, sessions, host };
}

function operation(
	fx: HostFixture,
	execute: () => { ok: boolean; stdout: string; stderr: string; status: number | null },
) {
	return runPiRegisteredOperation({
		ctx: fx.ctx,
		unit_id: "validate_import",
		attempt: 1,
		operation_id: "sure.onboard.execute_import",
		script_id: "run_validate.py",
		artifact_input_path: fx.artifactPath,
		request_operation: "validation",
		script_args: ["--kind", "import"],
		execute,
	});
}

function paths(fx: HostFixture): {
	root: string;
	request: string;
	receipt: string;
	admission: string;
	contract: string;
	immutable: string;
} {
	const session = fx.sessions[0];
	if (!session) throw new Error("host did not issue a session");
	const root = join(fx.ctx.runDir, "artifacts", "execution", "validate_import", session.invocation_id);
	return {
		root,
		request: join(root, "execution_request.json"),
		receipt: join(root, "execution_receipt.json"),
		admission: join(root, "execution_admission.json"),
		contract: join(root, "execution_contract.json"),
		immutable: join(root, "execution_contracts"),
	};
}

function readJson(path: string): Record<string, any> {
	return JSON.parse(readFileSync(path, "utf8")) as Record<string, any>;
}

function generatedContext(name: string, mutate?: (files: Record<string, any>) => void): SureHookContext {
	const root = join(TEMP_ROOT, name);
	const packageDir = join(root, "sure_onboard");
	const runDir = join(root, "run");
	mkdirSync(packageDir, { recursive: true });
	mkdirSync(join(runDir, "artifacts"), { recursive: true });
	const files: Record<string, any> = {};
	for (const file of [
		"generation.lock.json",
		"canonical-definition.json",
		"validator-registry.json",
		"semantic-backends.json",
		"executor-registry.json",
	]) {
		files[file] = readJson(join(GENERATED_PACKAGE, file));
		writeFileSync(join(packageDir, file), `${JSON.stringify(files[file])}\n`, "utf8");
	}
	mutate?.(files);
	for (const file of Object.keys(files))
		writeFileSync(join(packageDir, file), `${JSON.stringify(files[file])}\n`, "utf8");
	const lock = files["generation.lock.json"];
	return {
		point: "pre_start",
		run: {
			runId: name,
			command: "/sure_onboard",
			status: "running",
			runDir,
			outputDir: runDir,
			workflowDigest: lock.workflow_digest,
			validatorDigest: lock.validator_registry_digest,
			executorDigest: lock.executor_registry_digest,
			coreVersion: lock.core_package_version,
			policyDigest: DIGEST,
		} as never,
		skill: { name: "sure_onboard", command: "/sure_onboard" } as never,
		cwd: REPOSITORY_ROOT,
		packageDir,
		runDir,
		args: "",
		repoRoot: REPOSITORY_ROOT,
	};
}

afterEach(() => rmSync(TEMP_ROOT, { recursive: true, force: true }));

describe("Pi host-issued registered operation provenance", () => {
	it("publishes and rereads the request before callback execution, then produces a Core-valid history", () => {
		const fx = fixture("success");
		let requestVisible = false;
		const result = operation(fx, () => {
			const location = paths(fx);
			requestVisible = existsSync(location.request);
			const request = readJson(location.request);
			expect(request.request_id).toBe(fx.sessions[0]?.request_id);
			writeFileSync(fx.artifactPath, '{"stage":"after"}\n', "utf8");
			return { ok: true, stdout: "validated", stderr: "", status: 0 };
		});

		expect(requestVisible).toBe(true);
		expect(result.ok).toBe(true);
		expect(result.evidence).toMatchObject({
			verdict: "PASS",
			reason_code: "EXECUTION_SUCCEEDED",
			request_path: paths(fx).request,
		});
		const location = paths(fx);
		for (const path of [location.request, location.receipt, location.admission, location.contract]) {
			expect(existsSync(path), path).toBe(true);
		}
		const request = readJson(location.request);
		const receipt = readJson(location.receipt);
		const admission = readJson(location.admission);
		const contract = readJson(location.contract);
		const immutableRoot = location.immutable;
		const history = {
			latest: { request, receipt, admission, contract },
			immutable: {
				request: readJson(join(immutableRoot, `${request.request_id}.request.json`)),
				receipt: readJson(join(immutableRoot, `${request.request_id}.receipt.json`)),
				admission: readJson(join(immutableRoot, `${request.request_id}.admission.json`)),
				contract: readJson(join(immutableRoot, `${request.request_id}.contract.json`)),
			},
		};
		expect(
			validateExecutionContractHistory(history as never, {
				require_receipt: true,
				require_admission: true,
				require_contract_record: true,
				allowed_output_roots: [fx.ctx.runDir],
			}).valid,
		).toBe(true);
		expect(result.evidence?.request_digest).toBe(
			`sha256:${createHash("sha256").update(readFileSync(location.request)).digest("hex")}`,
		);
		expect(result.evidence?.receipt_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
		expect(result.evidence?.execution_history_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
	});

	it("uses a host-issued request dispatcher without invoking the skill callback", () => {
		const events: string[] = [];
		let dispatchedRequestId: string | undefined;
		const dispatcher: ExecutionRequestDispatcher = {
			probe(request, requirements) {
				events.push("probe");
				dispatchedRequestId = request.request_id;
				return requirements.map((requirement) => {
					const base: CapabilityEvidence = {
						capability_id: requirement.capability_id,
						capability_class: requirement.capability_class,
						status: "AVAILABLE",
						source: "host_probe",
						observed_at: NOW,
						details: { adapter: "test-host-dispatcher" },
					};
					return { ...base, evidence_digest: canonicalJsonDigest(base as unknown as JsonValue) };
				});
			},
			execute(request) {
				events.push("execute");
				const producesIndex = request.entrypoint.argv.indexOf("--produces");
				const outputPath = request.entrypoint.argv[producesIndex + 1];
				if (producesIndex < 0 || outputPath === undefined) throw new Error("dispatcher received no produces path");
				writeFileSync(outputPath, '{"stage":"dispatcher"}\n', "utf8");
				return { ok: true, stdout: "dispatched", stderr: "", status: 0 };
			},
		};
		const fx = fixture("host-dispatcher", undefined, undefined, dispatcher);
		let callbackInvoked = false;
		const result = operation(fx, () => {
			callbackInvoked = true;
			return { ok: true, stdout: "callback", stderr: "", status: 0 };
		});

		expect(callbackInvoked).toBe(false);
		expect(result.ok).toBe(true);
		expect(result.evidence).toMatchObject({ verdict: "PASS", reason_code: "EXECUTION_SUCCEEDED" });
		expect(dispatchedRequestId).toBe(fx.sessions[0]?.request_id);
		expect(events[0]).toBe("probe");
		expect(events).toContain("execute");
		expect(events.indexOf("execute")).toBeGreaterThan(events.indexOf("probe"));
	});

	it("blocks dispatcher execution when its host probe reports a missing capability", () => {
		const events: string[] = [];
		const dispatcher: ExecutionRequestDispatcher = {
			probe(request, requirements) {
				events.push(`probe:${request.request_id}`);
				return requirements.map((requirement) => {
					const base: CapabilityEvidence = {
						capability_id: requirement.capability_id,
						capability_class: requirement.capability_class,
						status: "MISSING",
						source: "host_probe",
						observed_at: NOW,
						details: { adapter: "test-host-dispatcher", reason: "hardware-unavailable" },
					};
					return { ...base, evidence_digest: canonicalJsonDigest(base as unknown as JsonValue) };
				});
			},
			execute() {
				events.push("execute");
				return { ok: true, stdout: "unexpected", stderr: "unexpected", status: 0 };
			},
		};
		const fx = fixture("host-dispatcher-missing", undefined, undefined, dispatcher);
		let callbackInvoked = false;
		const result = operation(fx, () => {
			callbackInvoked = true;
			return { ok: true, stdout: "unexpected", stderr: "", status: 0 };
		});

		expect(callbackInvoked).toBe(false);
		expect(events).toHaveLength(1);
		expect(events[0]).toMatch(/^probe:/);
		expect(result.ok).toBe(false);
		expect(result.status).toBeNull();
		expect(result.evidence).toMatchObject({ verdict: "NOT_EXECUTED", reason_code: "CAPABILITY_MISSING" });
		expect(existsSync(paths(fx).receipt)).toBe(false);
	});

	it("rejects malformed dispatcher evidence before execution", () => {
		const events: string[] = [];
		const dispatcher: ExecutionRequestDispatcher = {
			probe() {
				events.push("probe");
				return [{ malformed: true }] as unknown as CapabilityEvidence[];
			},
			execute() {
				events.push("execute");
				return { ok: true, stdout: "unexpected", stderr: "unexpected", status: 0 };
			},
		};
		const fx = fixture("host-dispatcher-malformed", undefined, undefined, dispatcher);
		let callbackInvoked = false;
		const result = operation(fx, () => {
			callbackInvoked = true;
			return { ok: true, stdout: "unexpected", stderr: "", status: 0 };
		});

		expect(callbackInvoked).toBe(false);
		expect(events).toEqual(["probe"]);
		expect(result.ok).toBe(false);
		expect(result.status).toBeNull();
		expect(result.evidence).toMatchObject({ verdict: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(existsSync(paths(fx).receipt)).toBe(false);
	});

	it("does not invoke the callback when request publication fails", () => {
		const fx = fixture("pre-publication-failure", () => {
			throw new Error("request publication refused");
		});
		let invoked = false;
		const result = operation(fx, () => {
			invoked = true;
			return { ok: true, stdout: "", stderr: "", status: 0 };
		});
		expect(invoked).toBe(false);
		expect(result.ok).toBe(false);
		expect(result.evidence).toMatchObject({ verdict: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(result.evidence?.request_digest).toBeUndefined();
	});

	it("reports a post-publication failure as non-PASS and preserves possible execution", () => {
		const fx = fixture(
			"post-publication-failure",
			(session) =>
				({
					publishRequest: (request: any) => session.publisher.publishRequest(request),
					publishCompletion: () => {
						throw new Error("receipt publication refused");
					},
				}) as unknown as ExecutionProvenancePublisher,
		);
		let invoked = false;
		const result = operation(fx, () => {
			invoked = true;
			writeFileSync(fx.artifactPath, '{"stage":"after"}\n', "utf8");
			return { ok: true, stdout: "validated", stderr: "", status: 0 };
		});
		expect(invoked).toBe(true);
		expect(result.ok).toBe(false);
		expect(result.evidence).toMatchObject({ verdict: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(result.evidence?.diagnostics.join(" ")).toContain("may have occurred");
		expect(result.evidence?.request_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
	});

	it("maps a callback with no started process to CAPABILITY_MISSING", () => {
		const fx = fixture("capability-missing");
		const result = operation(fx, () => ({ ok: false, stdout: "", stderr: "runtime missing", status: null }));
		expect(result.ok).toBe(false);
		expect(result.evidence).toMatchObject({ verdict: "NOT_EXECUTED", reason_code: "CAPABILITY_MISSING" });
		const location = paths(fx);
		expect(readJson(location.receipt).lifecycle).toBe("NOT_STARTED");
		expect(readJson(location.admission).status).toBe("CAPABILITY_MISSING");
	});

	it("blocks before the callback when the host reports a required capability missing", () => {
		const fx = fixture("preflight-capability-missing", undefined, (requirements) =>
			requirements.map((requirement) => ({
				capability_id: requirement.capability_id,
				capability_class: requirement.capability_class,
				status: "MISSING",
				source: "executor",
				observed_at: NOW,
				details: { probe: "test" },
			})),
		);
		let invoked = false;
		const result = operation(fx, () => {
			invoked = true;
			return { ok: true, stdout: "unexpected", stderr: "", status: 0 };
		});

		expect(invoked).toBe(false);
		expect(result.ok).toBe(false);
		expect(result.status).toBeNull();
		expect(result.evidence).toMatchObject({ verdict: "NOT_EXECUTED", reason_code: "CAPABILITY_MISSING" });
		const location = paths(fx);
		expect(existsSync(location.request)).toBe(true);
		expect(existsSync(location.admission)).toBe(true);
		expect(existsSync(location.contract)).toBe(true);
		expect(existsSync(location.receipt)).toBe(false);
		expect(readJson(location.admission)).toMatchObject({
			status: "CAPABILITY_MISSING",
			probe_invoked: true,
			execute_invoked: false,
			receipt_present: false,
			receipt_valid: false,
		});
		const request = readJson(location.request);
		const admission = readJson(location.admission);
		const contract = readJson(location.contract);
		expect(
			validateExecutionContractHistory(
				{
					latest: { request, admission, contract },
					immutable: {
						request: readJson(join(location.immutable, `${request.request_id}.request.json`)),
						admission: readJson(join(location.immutable, `${request.request_id}.admission.json`)),
						contract: readJson(join(location.immutable, `${request.request_id}.contract.json`)),
					},
				} as never,
				{
					require_receipt: false,
					require_admission: true,
					require_contract_record: true,
					allowed_output_roots: [fx.ctx.runDir],
				},
			).valid,
		).toBe(true);
	});

	it("preserves an authoritative capability denial without invoking the callback", () => {
		const fx = fixture("preflight-capability-denied", undefined, (requirements) =>
			requirements.map((requirement) => ({
				capability_id: requirement.capability_id,
				capability_class: requirement.capability_class,
				status: "DENIED",
				source: "executor",
				observed_at: NOW,
				details: { policy: "test-deny" },
			})),
		);
		let invoked = false;
		const result = operation(fx, () => {
			invoked = true;
			return { ok: true, stdout: "unexpected", stderr: "", status: 0 };
		});

		expect(invoked).toBe(false);
		expect(result.ok).toBe(false);
		expect(result.evidence).toMatchObject({ verdict: "NOT_EXECUTED", reason_code: "POLICY_DENIED" });
		const location = paths(fx);
		expect(existsSync(location.receipt)).toBe(false);
		expect(readJson(location.admission)).toMatchObject({ execute_invoked: false, receipt_present: false });
	});

	it("rejects malformed host capability evidence before invoking the callback", () => {
		const fx = fixture(
			"preflight-capability-invalid",
			undefined,
			() => [{ malformed: true }] as unknown as CapabilityEvidence[],
		);
		let invoked = false;
		const result = operation(fx, () => {
			invoked = true;
			return { ok: true, stdout: "unexpected", stderr: "", status: 0 };
		});

		expect(invoked).toBe(false);
		expect(result.ok).toBe(false);
		expect(result.status).toBeNull();
		expect(result.evidence).toMatchObject({ verdict: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		const location = paths(fx);
		expect(existsSync(location.request)).toBe(true);
		expect(existsSync(location.admission)).toBe(true);
		expect(existsSync(location.contract)).toBe(true);
		expect(existsSync(location.receipt)).toBe(false);
		expect(readJson(location.admission)).toMatchObject({
			status: "REJECTED",
			probe_invoked: true,
			execute_invoked: false,
			receipt_present: false,
			receipt_valid: false,
		});
	});

	it("maps a nonzero process exit to FAIL without advancing it to PASS", () => {
		const fx = fixture("nonzero-exit");
		const result = operation(fx, () => ({ ok: false, stdout: "", stderr: "validator failed", status: 7 }));

		expect(result.ok).toBe(false);
		expect(result.status).toBe(7);
		expect(result.evidence).toMatchObject({ verdict: "FAIL", reason_code: "EXECUTION_FAILED" });
		const location = paths(fx);
		expect(readJson(location.receipt)).toMatchObject({ lifecycle: "FAILED", exit_code: 7 });
		expect(readJson(location.admission)).toMatchObject({ status: "ADMITTED", execute_invoked: true });
	});

	it("does not turn a zero exit without the bound artifact into PASS", () => {
		const fx = fixture("missing-output");
		const result = operation(fx, () => {
			rmSync(fx.artifactPath);
			return { ok: true, stdout: "", stderr: "", status: 0 };
		});
		expect(result.ok).toBe(false);
		expect(result.evidence).toMatchObject({ verdict: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
		expect(readJson(paths(fx).receipt).lifecycle).toBe("PARTIAL");
	});

	it("rejects a host binding drift before callback execution", () => {
		const fx = fixture("binding-drift");
		const original = fx.host.issue;
		fx.ctx.executionProvenance = {
			issue(input) {
				return { ...(original(input) as any), run_id: "different-run" };
			},
		};
		let invoked = false;
		const result = operation(fx, () => {
			invoked = true;
			return { ok: true, stdout: "", stderr: "", status: 0 };
		});
		expect(invoked).toBe(false);
		expect(result.evidence).toMatchObject({ verdict: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
	});

	it("rejects a tampered receipt without allowing a fake PASS", () => {
		const fx = fixture(
			"receipt-tamper",
			(session) =>
				({
					publishRequest: (request: any) => session.publisher.publishRequest(request),
					publishCompletion: (input: any) =>
						session.publisher.publishCompletion({
							...input,
							receipt: { ...input.receipt, request_id: "tampered-request" },
						}),
				}) as unknown as ExecutionProvenancePublisher,
		);
		const result = operation(fx, () => ({ ok: true, stdout: "validated", stderr: "", status: 0 }));
		expect(result.ok).toBe(false);
		expect(result.evidence).toMatchObject({ verdict: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
	});

	it("refuses a request id collision without invoking the second callback", () => {
		const fx = fixture("collision");
		const sessions: Array<Record<string, any>> = [];
		const collisionBase = createPiExecutionProvenanceHost({
			run: { runId: "collision", runDir: fx.ctx.runDir, outputDir: fx.ctx.runDir },
			package_dir: fx.ctx.packageDir,
			skill_id: "sure_onboard",
			workflow_digest: DIGEST,
			validator_registry_digest: DIGEST,
			semantic_runtime_digest: DIGEST,
			semantic_backend_registry_digest: (readJson(REGISTRY) as any).registry_digest,
			executor_registry_digest: DIGEST,
			core_package_version: "0.80.3",
			reference_snapshot_digest: DIGEST,
			policy_digest: DIGEST,
			python_executable: process.execPath,
			new_id: () => "same-id",
		});
		fx.ctx.run.runId = "collision";
		fx.ctx.executionProvenance = {
			issue(input) {
				const session = collisionBase.issue(input) as any;
				sessions.push(session);
				return session;
			},
		};
		const first = operation(fx, () => ({ ok: true, stdout: "", stderr: "", status: 0 }));
		let secondInvoked = false;
		const second = operation(fx, () => {
			secondInvoked = true;
			return { ok: true, stdout: "", stderr: "", status: 0 };
		});
		expect(first.ok).toBe(true);
		expect(secondInvoked).toBe(false);
		expect(second.evidence).toMatchObject({ verdict: "NOT_EXECUTED", reason_code: "INVALID_CONTRACT" });
	});

	it("accepts a complete generated binding only when its package registries agree with the lock", () => {
		const ctx = generatedContext("generated-binding");
		const host = createPiExecutionProvenanceHostForContext(ctx as Omit<SureHookContext, "point">);
		expect(host).toBeDefined();
		if (host === undefined) throw new Error("expected a generated Pi provenance host");
		const session = host.issue({
			unit_id: "load_model_input",
			attempt: 1,
			operation_id: "sure.onboard.execute_import",
		});
		expect(session.semantic_backend_registry_digest).toBe(
			readJson(join(GENERATED_PACKAGE, "generation.lock.json")).semantic_backend_registry_digest,
		);
	});

	it("forwards an explicitly host-supplied dispatcher through a generated binding", () => {
		const ctx = generatedContext("generated-dispatcher");
		const dispatcher: ExecutionRequestDispatcher = {
			probe: () => [],
			execute: () => ({ ok: false, stdout: "", stderr: "not used", status: null }),
		};
		const host = createPiExecutionProvenanceHostForContext(ctx as Omit<SureHookContext, "point">, {
			execution_dispatcher: dispatcher,
		});
		expect(host).toBeDefined();
		if (host === undefined) throw new Error("expected a generated Pi provenance host");
		const session = host.issue({
			unit_id: "load_model_input",
			attempt: 1,
			operation_id: "sure.onboard.execute_import",
		});
		expect(session.execution_dispatcher).toBe(dispatcher);
	});

	it("rejects a generated package whose canonical definition was changed beside the lock", () => {
		const ctx = generatedContext("generated-definition-drift", (files) => {
			files["canonical-definition.json"].description = "agent supplied definition";
		});
		const host = createPiExecutionProvenanceHostForContext(ctx as Omit<SureHookContext, "point">);
		expect(host).toBeDefined();
		expect(() =>
			host?.issue({ unit_id: "load_model_input", attempt: 1, operation_id: "sure.onboard.execute_import" }),
		).toThrow(/canonical definition digest/);
	});
});
