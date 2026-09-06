import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseMemoryUri, validateMemoryContract } from "../src/index.ts";

const repositoryRoot = fileURLToPath(new URL("../../../", import.meta.url));

describe("host-neutral memory contract", () => {
	it("validates the canonical contract and computes a stable digest", () => {
		const value = JSON.parse(
			readFileSync(`${repositoryRoot}/sure/canonical/shared/memory-contract.json`, "utf8"),
		) as unknown;
		const result = validateMemoryContract(value);
		expect(result.valid).toBe(true);
		expect(result.errors).toEqual([]);
		expect(result.digest).toMatch(/^sha256:[0-9a-f]{64}$/);
	});

	it("rejects an invalid projection and unsafe logical URI", () => {
		const invalid = validateMemoryContract({ schema: "sure.memory.contract.v1" });
		expect(invalid.valid).toBe(false);
		expect(invalid.errors.length).toBeGreaterThan(1);
		expect(() => parseMemoryUri("memory://sure_feed/fact/not-shared")).toThrow(/_shared/);
		expect(() => parseMemoryUri("memory://sure_feed/bad_case/../escape")).toThrow(/skill\/kind\/slug/);
	});

	it("accepts only namespace-correct logical identities", () => {
		expect(parseMemoryUri("memory://_shared/fact/site-gpu")).toEqual({
			skill: "_shared",
			kind: "fact",
			slug: "site-gpu",
		});
		expect(parseMemoryUri("memory://sure_eval/bad_case/protocol-mismatch")).toEqual({
			skill: "sure_eval",
			kind: "bad_case",
			slug: "protocol-mismatch",
		});
	});
});
