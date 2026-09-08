import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { dispatchExecutor, ExecutionProvenancePublisher, ExecutorRegistry } from "../src/index.ts";
import { NodeExecutionProvenancePublicationPort } from "../src/node/index.ts";
import { FIXTURE_NOW, vcAdapterFixture } from "./external-adapter-fixture.ts";

const roots: string[] = [];

function temporaryRoot(prefix: string): string {
	const root = mkdtempSync(join(tmpdir(), prefix));
	roots.push(root);
	return root;
}

afterEach(() => {
	for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("NodeExecutionProvenancePublicationPort", () => {
	it("writes the shared latest and immutable wire contract", async () => {
		const workspace = temporaryRoot("sure-provenance-node-");
		const publicationRoot = join(workspace, "run", "invocations", "execute-1");
		const fixture = vcAdapterFixture();
		const registry = new ExecutorRegistry();
		registry.registerExternal(fixture.port, fixture.binding);
		const publisher = new ExecutionProvenancePublisher(
			new NodeExecutionProvenancePublicationPort({ root: publicationRoot, allowed_roots: [workspace] }),
		);
		publisher.publishRequest(fixture.request);
		const execution = await dispatchExecutor(registry, fixture.request, { now: () => FIXTURE_NOW });
		if (execution.receipt === undefined) throw new Error("fixture did not produce a receipt");
		const published = publisher.publishCompletion({
			request: fixture.request,
			receipt: execution.receipt,
			admission: execution.admission_trace,
		});

		for (const document of ["request", "receipt", "admission", "contract"] as const) {
			expect(existsSync(join(publicationRoot, `execution_${document}.json`))).toBe(true);
			expect(
				existsSync(join(publicationRoot, "execution_contracts", `${fixture.request.request_id}.${document}.json`)),
			).toBe(true);
		}
		expect(JSON.parse(readFileSync(join(publicationRoot, "execution_contract.json"), "utf8"))).toMatchObject({
			schema: "sure.execution_compatibility.v1",
			admission_instrumentation: "admission-v1",
			contract_valid: true,
		});
		expect(published.validation.valid).toBe(true);
	});

	it("rejects a read-only reference target before creating it", () => {
		const workspace = temporaryRoot("sure-provenance-policy-");
		const reference = join(workspace, "reference");
		const target = join(reference, "must-not-exist");
		mkdirSync(reference);

		expect(
			() =>
				new NodeExecutionProvenancePublicationPort({
					root: target,
					allowed_roots: [workspace],
					forbidden_roots: [reference],
				}),
		).toThrow(/read-only reference root/);
		expect(existsSync(target)).toBe(false);
	});

	it("rejects a symlink-parent escape before writing outside the admitted root", () => {
		const workspace = temporaryRoot("sure-provenance-symlink-");
		const outside = temporaryRoot("sure-provenance-outside-");
		const link = join(workspace, "linked");
		const escapedTarget = join(outside, "must-not-exist");
		symlinkSync(outside, link, "dir");

		expect(
			() =>
				new NodeExecutionProvenancePublicationPort({
					root: join(link, "must-not-exist"),
					allowed_roots: [workspace],
				}),
		).toThrow(/SYMLINK_ESCAPE/);
		expect(existsSync(escapedTarget)).toBe(false);
	});

	it("detects replacement of the immutable-document directory", () => {
		const workspace = temporaryRoot("sure-provenance-swap-");
		const outside = temporaryRoot("sure-provenance-swap-outside-");
		const publicationRoot = join(workspace, "publication");
		const port = new NodeExecutionProvenancePublicationPort({ root: publicationRoot, allowed_roots: [workspace] });
		const historyRoot = join(publicationRoot, "execution_contracts");
		rmSync(historyRoot, { recursive: true });
		symlinkSync(outside, historyRoot, "dir");

		expect(() => new ExecutionProvenancePublisher(port).publishRequest(vcAdapterFixture().request)).toThrow(
			/parent must be a regular directory/,
		);
		expect(existsSync(join(outside, "dispatch-remote.request.json"))).toBe(false);
	});

	it("refuses to replace a symlink used as a latest document", () => {
		const workspace = temporaryRoot("sure-provenance-latest-link-");
		const publicationRoot = join(workspace, "publication");
		const outside = join(workspace, "outside.json");
		mkdirSync(publicationRoot, { recursive: true });
		writeFileSync(outside, "{}\n");
		const port = new NodeExecutionProvenancePublicationPort({ root: publicationRoot, allowed_roots: [workspace] });
		symlinkSync(outside, join(publicationRoot, "execution_request.json"), "file");

		expect(() => new ExecutionProvenancePublisher(port).publishRequest(vcAdapterFixture().request)).toThrow(
			/regular file/,
		);
		expect(() =>
			port.writeLatest(
				{ view: "latest", request_id: "dispatch-remote", document: "request" },
				'{"replacement":true}\n',
			),
		).toThrow(/non-regular latest execution provenance/);
		expect(readFileSync(outside, "utf8")).toBe("{}\n");
	});
});
