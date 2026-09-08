import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import {
	dispatchExecutor,
	type ExecutionProvenanceDocumentKey,
	ExecutionProvenancePublicationError,
	type ExecutionProvenancePublicationPort,
	ExecutionProvenancePublisher,
	ExecutorRegistry,
} from "../src/index.ts";
import type { RunStoreLock } from "../src/run/types.ts";
import { FIXTURE_NOW, vcAdapterFixture } from "./external-adapter-fixture.ts";

class MemoryProvenancePort implements ExecutionProvenancePublicationPort {
	readonly files = new Map<string, string>();
	readonly lock: RunStoreLock = { withLock: <T>(_key: string, operation: () => T) => operation() };
	corruptDocument?: ExecutionProvenanceDocumentKey["document"];
	aliasLocations = false;

	location(key: ExecutionProvenanceDocumentKey): string {
		if (this.aliasLocations) return "memory://aliased";
		return key.view === "latest"
			? `memory://latest/${key.document}`
			: `memory://immutable/${key.request_id}/${key.document}`;
	}

	read(key: ExecutionProvenanceDocumentKey): string | undefined {
		const content = this.files.get(this.location(key));
		if (content !== undefined && key.document === this.corruptDocument) return content.trimEnd();
		return content;
	}

	fileType(key: ExecutionProvenanceDocumentKey): "missing" | "file" {
		return this.files.has(this.location(key)) ? "file" : "missing";
	}

	digest(key: ExecutionProvenanceDocumentKey): string | undefined {
		const content = this.read(key);
		return content === undefined ? undefined : `sha256:${createHash("sha256").update(content).digest("hex")}`;
	}

	writeLatest(key: ExecutionProvenanceDocumentKey, content: string): void {
		this.files.set(this.location(key), content);
	}

	writeImmutable(key: ExecutionProvenanceDocumentKey, content: string): void {
		const location = this.location(key);
		const existing = this.files.get(location);
		if (existing !== undefined && existing !== content) {
			throw new Error(`refusing to replace immutable execution provenance: ${location}`);
		}
		this.files.set(location, content);
	}
}

function registryFixture() {
	const fixture = vcAdapterFixture();
	const registry = new ExecutorRegistry();
	registry.registerExternal(fixture.port, fixture.binding);
	return { fixture, registry };
}

describe("ExecutionProvenancePublisher", () => {
	it("publishes the request before execution and verifies a complete latest/immutable history", async () => {
		const { fixture, registry } = registryFixture();
		const port = new MemoryProvenancePort();
		const publisher = new ExecutionProvenancePublisher(port);
		const request = publisher.publishRequest(fixture.request);

		expect(request.request_id).toBe(fixture.request.request_id);
		expect(port.files.has("memory://latest/request")).toBe(true);
		expect(port.files.has(`memory://immutable/${fixture.request.request_id}/request`)).toBe(true);
		expect(port.files.has("memory://latest/receipt")).toBe(false);

		const execution = await dispatchExecutor(registry, fixture.request, { now: () => FIXTURE_NOW });
		if (execution.receipt === undefined) throw new Error("fixture did not produce a receipt");
		const published = publisher.publishCompletion({
			request: fixture.request,
			receipt: execution.receipt,
			admission: execution.admission_trace,
			validation_options: { require_receipt: true },
		});

		expect(published.validation.valid).toBe(true);
		expect(published.validation.outcome).toMatchObject({
			outcome: "NOT_EXECUTED",
			reason_code: "VALIDATION_PENDING",
		});
		expect(published.documents.receipt).toBeDefined();
		expect(published.documents.contract.immutable.path).toBe(
			`memory://immutable/${fixture.request.request_id}/contract`,
		);
		expect(published.history.latest.contract?.admission_instrumentation).toBe("admission-v1");
		expect(published.history_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
	});

	it("persists capability absence as NOT_EXECUTED without inventing a receipt", async () => {
		const { fixture } = registryFixture();
		const port = new MemoryProvenancePort();
		const publisher = new ExecutionProvenancePublisher(port);
		publisher.publishRequest(fixture.request);
		const execution = await dispatchExecutor(new ExecutorRegistry(), fixture.request, { now: () => FIXTURE_NOW });
		const published = publisher.publishCompletion({
			request: fixture.request,
			admission: execution.admission_trace,
			validation_options: { require_receipt: true },
		});

		expect(published.validation.valid).toBe(true);
		expect(published.validation.outcome).toMatchObject({
			outcome: "NOT_EXECUTED",
			reason_code: "CAPABILITY_MISSING",
		});
		expect(published.documents.receipt).toBeUndefined();
		expect(port.files.has("memory://latest/receipt")).toBe(false);
		expect(port.files.has(`memory://immutable/${fixture.request.request_id}/receipt`)).toBe(false);
	});

	it("requires request publication before completion", async () => {
		const { fixture, registry } = registryFixture();
		const execution = await dispatchExecutor(registry, fixture.request, { now: () => FIXTURE_NOW });
		if (execution.receipt === undefined) throw new Error("fixture did not produce a receipt");

		expect(() =>
			new ExecutionProvenancePublisher(new MemoryProvenancePort()).publishCompletion({
				request: fixture.request,
				receipt: execution.receipt,
				admission: execution.admission_trace,
			}),
		).toThrow(ExecutionProvenancePublicationError);
	});

	it("refuses an immutable request-id collision", () => {
		const { fixture } = registryFixture();
		const publisher = new ExecutionProvenancePublisher(new MemoryProvenancePort());
		publisher.publishRequest(fixture.request);

		expect(() => publisher.publishRequest({ ...fixture.request, created_at: "2026-09-07T00:00:01.000Z" })).toThrow(
			/refusing to replace.*execution provenance/,
		);
	});

	it("normalizes a semantically identical caller-authored latest request", () => {
		const { fixture } = registryFixture();
		const port = new MemoryProvenancePort();
		port.files.set("memory://latest/request", JSON.stringify(fixture.request));
		const publisher = new ExecutionProvenancePublisher(port);

		const published = publisher.publishRequest(fixture.request);

		expect(port.files.get("memory://latest/request")).toBe(`${JSON.stringify(fixture.request, null, 2)}\n`);
		expect(published.documents.latest.digest).toMatch(/^sha256:[0-9a-f]{64}$/);
		expect(port.files.has(`memory://immutable/${fixture.request.request_id}/request`)).toBe(true);
	});

	it("refuses to reuse one latest view for a different request id", () => {
		const { fixture } = registryFixture();
		const port = new MemoryProvenancePort();
		const publisher = new ExecutionProvenancePublisher(port);
		publisher.publishRequest(fixture.request);
		const second = { ...fixture.request, request_id: "dispatch-second" };

		expect(() => publisher.publishRequest(second)).toThrow(/request already published/);
		expect(port.files.has("memory://immutable/dispatch-second/request")).toBe(false);
	});

	it("fails closed when persisted bytes do not survive an exact reread", async () => {
		const { fixture, registry } = registryFixture();
		const port = new MemoryProvenancePort();
		const publisher = new ExecutionProvenancePublisher(port);
		publisher.publishRequest(fixture.request);
		const execution = await dispatchExecutor(registry, fixture.request, { now: () => FIXTURE_NOW });
		if (execution.receipt === undefined) throw new Error("fixture did not produce a receipt");
		port.corruptDocument = "contract";

		expect(() =>
			publisher.publishCompletion({
				request: fixture.request,
				receipt: execution.receipt,
				admission: execution.admission_trace,
			}),
		).toThrow(/bytes changed during publication/);
	});

	it("retains an invalid receipt as evidence instead of dropping its diagnostic bundle", async () => {
		const { fixture, registry } = registryFixture();
		const port = new MemoryProvenancePort();
		const publisher = new ExecutionProvenancePublisher(port);
		publisher.publishRequest(fixture.request);
		const execution = await dispatchExecutor(registry, fixture.request, { now: () => FIXTURE_NOW });
		if (execution.receipt === undefined) throw new Error("fixture did not produce a receipt");
		const invalidReceipt = { ...execution.receipt, policy_digest: `sha256:${"f".repeat(64)}` };
		const published = publisher.publishCompletion({
			request: fixture.request,
			receipt: invalidReceipt,
			admission: execution.admission_trace,
		});

		expect(published.validation.valid).toBe(false);
		expect(published.validation.outcome).toMatchObject({
			outcome: "NOT_EXECUTED",
			reason_code: "INVALID_CONTRACT",
		});
		expect(published.documents.receipt).toBeDefined();
	});

	it("rejects adapters that alias distinct contract documents", () => {
		const { fixture } = registryFixture();
		const port = new MemoryProvenancePort();
		port.aliasLocations = true;

		expect(() => new ExecutionProvenancePublisher(port).publishRequest(fixture.request)).toThrow(
			/adapt.*aliased distinct documents/,
		);
		expect(port.files.size).toBe(0);
	});
});
