import { describe, expect, test } from "vitest";
import { isTruthyEnvFlag } from "../src/main.ts";

describe("isTruthyEnvFlag", () => {
	test("reads a flag that carries surrounding whitespace", () => {
		// A value exported from a script, a .env line or a Windows `set` keeps
		// the trailing blank or newline the author never meant to include.
		expect(isTruthyEnvFlag(" 1")).toBe(true);
		expect(isTruthyEnvFlag("1\n")).toBe(true);
		expect(isTruthyEnvFlag("true\r\n")).toBe(true);
		expect(isTruthyEnvFlag("\tyes ")).toBe(true);
	});

	test("stays false for unset, blank and negative values", () => {
		expect(isTruthyEnvFlag(undefined)).toBe(false);
		expect(isTruthyEnvFlag("")).toBe(false);
		expect(isTruthyEnvFlag("   ")).toBe(false);
		expect(isTruthyEnvFlag("0")).toBe(false);
		expect(isTruthyEnvFlag("off")).toBe(false);
	});
});
