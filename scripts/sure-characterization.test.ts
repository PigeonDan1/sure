import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
	assertReferenceOutputOutside,
	buildReferenceCatalog,
	buildRepositoryBaseline,
	semanticDigest,
	writeReferenceCatalog,
	writeRepositoryArtifacts,
} from "./sure-characterization.ts";

function treeDigest(root: string): string {
	const hash = createHash("sha256");
	const walk = (directory: string, prefix: string): void => {
		for (const entry of readdirSync(directory, { withFileTypes: true }).sort((a, b) =>
			a.name.localeCompare(b.name),
		)) {
			const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name;
			hash.update(`${entry.isDirectory() ? "d" : entry.isSymbolicLink() ? "l" : "f"}:${relativePath}\0`);
			if (entry.isDirectory()) walk(join(directory, entry.name), relativePath);
			else if (entry.isFile()) hash.update(readFileSync(join(directory, entry.name)));
		}
	};
	walk(root, "");
	return hash.digest("hex");
}

function syntheticReference(): { root: string; targets: { logicalPath: string; roles: string[]; expect: "file" }[] } {
	const root = mkdtempSync(join(tmpdir(), "sure-reference-"));
	mkdirSync(join(root, "models", "model-a", "artifacts"), { recursive: true });
	mkdirSync(join(root, "results", "model-a", "standard_system"), { recursive: true });
	writeFileSync(
		join(root, "models", "model-a", "artifacts", "verdict.json"),
		`${JSON.stringify({ schema: "sure.test.v1", status: "success", secret_payload: "not-catalogued" })}\n`,
	);
	writeFileSync(
		join(root, "results", "model-a", "standard_system", "protocol.yaml"),
		"protocol_id: standard_system\n",
	);
	return {
		root,
		targets: [
			{
				logicalPath: "models/model-a/artifacts/verdict.json",
				roles: ["sure-onboard", "success"],
				expect: "file",
			},
		],
	};
}

test("repository characterization is byte-for-byte current", () => {
	assert.doesNotThrow(() => writeRepositoryArtifacts(true));
});

test("repository characterization has a stable semantic digest", () => {
	const first = buildRepositoryBaseline();
	const second = buildRepositoryBaseline();
	assert.equal(first.semantic_digest, second.semantic_digest);
	assert.equal(first.semantic_digest, semanticDigest(first));
	const changed = structuredClone(first);
	changed.purpose = "intentional golden change";
	assert.notEqual(semanticDigest(changed), first.semantic_digest);
});

test("reference catalog is deterministic and leaves its source unchanged", () => {
	const fixture = syntheticReference();
	const options = {
		referenceRoot: fixture.root,
		observedAt: "2000-01-01T00:00:00.000Z",
		targets: fixture.targets,
	};
	const before = treeDigest(fixture.root);
	const first = buildReferenceCatalog(options);
	const second = buildReferenceCatalog(options);
	assert.equal(first.semantic_digest, second.semantic_digest);
	assert.equal(first.semantic_digest, semanticDigest(first));
	assert.equal(treeDigest(fixture.root), before);
	assert.equal(JSON.stringify(first).includes("not-catalogued"), false);

	const output = join(mkdtempSync(join(tmpdir(), "sure-catalog-output-")), "catalog.json");
	writeReferenceCatalog(options, output);
	assert.equal(treeDigest(fixture.root), before);
	assert.equal(JSON.parse(readFileSync(output, "utf8")).semantic_digest, first.semantic_digest);
});

test("reference catalog refuses lexical and symlinked output paths inside the source", () => {
	const fixture = syntheticReference();
	assert.throws(
		() => assertReferenceOutputOutside(fixture.root, join(fixture.root, "catalog.json")),
		/must not be written inside/,
	);
	const aliasRoot = mkdtempSync(join(tmpdir(), "sure-reference-alias-"));
	const alias = join(aliasRoot, "production");
	symlinkSync(fixture.root, alias, "dir");
	assert.throws(
		() => assertReferenceOutputOutside(fixture.root, join(alias, "catalog.json")),
		/resolves inside.*symlink/,
	);
});
