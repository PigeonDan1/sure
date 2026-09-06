import { describe, expect, it } from "vitest";
import { createPolicySnapshot, type PolicySnapshotInput, validatePolicySnapshot } from "../src/index.ts";

function input(): PolicySnapshotInput {
	return {
		site_id: "local-test",
		policy_version: 1,
		policy: { schema: "sure.site.policy.v1", storage: { runtime_root: "/runtime" } },
		source: { kind: "environment", path: "/config/site.yaml", raw_sha256: "a".repeat(64) },
		path_bindings: [
			{ root_id: "runtime", role: "runtime_cache", path: "/runtime", resolved_path: "/runtime" },
			{ root_id: "models", role: "read_only_reference", path: "/models", resolved_path: "/models" },
		],
	};
}

describe("policy snapshot", () => {
	it("normalizes source digests and orders path bindings deterministically", () => {
		const first = createPolicySnapshot(input());
		const reversed = input();
		reversed.path_bindings = [...reversed.path_bindings].reverse();
		const second = createPolicySnapshot(reversed);
		expect(first).toEqual(second);
		expect(first.source.raw_sha256).toBe(`sha256:${"a".repeat(64)}`);
		expect(first.path_bindings.map((binding) => binding.root_id)).toEqual(["models", "runtime"]);
		expect(first.snapshot_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
		expect(first.policy_digest).not.toBe(first.bindings_digest);
	});

	it("rejects duplicate, relative, and non-normalized bindings", () => {
		const duplicate = input();
		duplicate.path_bindings = [...duplicate.path_bindings, duplicate.path_bindings[0]];
		expect(() => createPolicySnapshot(duplicate)).toThrow(/Duplicate policy root id/);

		const relative = input();
		relative.path_bindings = [{ root_id: "bad", role: "dataset_source", path: "data", resolved_path: "/data" }];
		expect(() => createPolicySnapshot(relative)).toThrow(/must be absolute/);

		const normalized = input();
		normalized.path_bindings = [
			{ root_id: "bad", role: "dataset_source", path: "/data/../other", resolved_path: "/other" },
		];
		expect(() => createPolicySnapshot(normalized)).toThrow(/must be normalized/);
	});

	it("rejects invalid source evidence", () => {
		const invalid = input();
		invalid.source = { kind: "environment", path: "site.yaml", raw_sha256: "not-a-digest" };
		expect(() => createPolicySnapshot(invalid)).toThrow(/digest/);
	});

	it("recomputes persisted digests and rejects tampered snapshots", () => {
		const snapshot = createPolicySnapshot(input());
		expect(validatePolicySnapshot(JSON.parse(JSON.stringify(snapshot)))).toEqual(snapshot);

		const tamperedPolicy = { ...snapshot, policy: { changed: true } };
		expect(() => validatePolicySnapshot(tamperedPolicy)).toThrow(/policy_digest|canonical contents/);

		const unknownField = { ...snapshot, unexpected: true };
		expect(() => validatePolicySnapshot(unknownField)).toThrow(/unknown field/);
	});

	it("requires normalized absolute source paths", () => {
		const snapshot = createPolicySnapshot(input());
		const tampered = {
			...snapshot,
			source: { ...snapshot.source, path: "/config/../site.yaml" },
		};
		expect(() => validatePolicySnapshot(tampered)).toThrow(/absolute normalized/);
	});
});
