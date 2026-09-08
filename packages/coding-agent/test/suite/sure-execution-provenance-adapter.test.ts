import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { dispatchExecutor, ExecutionProvenancePublisher, ExecutorRegistry } from "@earendil-works/sure-core";
import { NodeExecutionProvenancePublicationPort } from "@earendil-works/sure-core/node";
import { afterEach, describe, expect, it } from "vitest";
import { FIXTURE_NOW, vcAdapterFixture } from "../../../sure-core/test/external-adapter-fixture.ts";
import { createPiExecutionProvenancePublisher } from "../../src/core/sure/execution-provenance.ts";

const roots: string[] = [];

function temporaryRoot(prefix: string): string {
	const root = mkdtempSync(join(tmpdir(), prefix));
	roots.push(root);
	return root;
}

function stableContract(record: Record<string, unknown> | undefined): Record<string, unknown> | undefined {
	if (record === undefined) return undefined;
	const {
		request_path: _requestPath,
		receipt_path: _receiptPath,
		admission_path: _admissionPath,
		history: _history,
		...stable
	} = record;
	return stable;
}

afterEach(() => {
	for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("Pi execution provenance adapter", () => {
	it("publishes the same non-location contract semantics as the portable Node adapter", async () => {
		const workspace = temporaryRoot("sure-pi-provenance-");
		const runDir = join(workspace, "pi-run");
		const portableRoot = join(workspace, "portable-run", "execution");
		const pi = createPiExecutionProvenancePublisher({
			run: { runDir },
			unit_id: "execute",
			invocation_id: "invocation-1",
		});
		const portable = new ExecutionProvenancePublisher(
			new NodeExecutionProvenancePublicationPort({ root: portableRoot, allowed_roots: [workspace] }),
		);
		const fixture = vcAdapterFixture();
		const registry = new ExecutorRegistry();
		registry.registerExternal(fixture.port, fixture.binding);
		const execution = await dispatchExecutor(registry, fixture.request, { now: () => FIXTURE_NOW });
		if (execution.receipt === undefined) throw new Error("fixture did not produce a receipt");

		pi.publisher.publishRequest(fixture.request);
		portable.publishRequest(fixture.request);
		const piPublished = pi.publisher.publishCompletion({
			request: fixture.request,
			receipt: execution.receipt,
			admission: execution.admission_trace,
		});
		const portablePublished = portable.publishCompletion({
			request: fixture.request,
			receipt: execution.receipt,
			admission: execution.admission_trace,
		});

		expect(piPublished.history.latest.request).toEqual(portablePublished.history.latest.request);
		expect(piPublished.history.latest.receipt).toEqual(portablePublished.history.latest.receipt);
		expect(piPublished.history.latest.admission).toEqual(portablePublished.history.latest.admission);
		expect(stableContract(piPublished.history.latest.contract)).toEqual(
			stableContract(portablePublished.history.latest.contract),
		);
		expect(piPublished.validation.outcome).toEqual(portablePublished.validation.outcome);
		expect(JSON.parse(readFileSync(join(pi.root, "execution_contract.json"), "utf8"))).toMatchObject({
			schema: "sure.execution_compatibility.v1",
			admission_instrumentation: "admission-v1",
		});
	});

	it("does not create a publication root beneath a forbidden reference tree", () => {
		const referenceRun = temporaryRoot("sure-pi-provenance-reference-");

		expect(() =>
			createPiExecutionProvenancePublisher({
				run: { runDir: referenceRun },
				unit_id: "execute",
				invocation_id: "invocation-1",
				forbidden_output_roots: [referenceRun],
			}),
		).toThrow(/read-only reference root/);
	});
});
