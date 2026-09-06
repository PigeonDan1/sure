import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { PortableRuntimeVerificationError, verifyPortableRuntime } from "../src/evaluation/portable-runtime.ts";

const repositoryRoot = fileURLToPath(new URL("../../..", import.meta.url));
const generatedRuntime = join(repositoryRoot, "sure", "dist", "portable-runtime");

function copiedRuntime(): { parent: string; root: string } {
	const parent = mkdtempSync(join(tmpdir(), "sure-portable-runtime-"));
	const root = join(parent, "runtime");
	cpSync(generatedRuntime, root, { recursive: true });
	return { parent, root };
}

describe("portable runtime verification", () => {
	it("verifies the generated runtime and all registry bindings", () => {
		const verification = verifyPortableRuntime(generatedRuntime, { expected_core_package_version: "0.80.3" });
		expect(verification.lock.runtime_digest).toMatch(/^sha256:[0-9a-f]{64}$/);
		expect(verification.lock.files.some((file) => file.path === ".sure-generated")).toBe(true);
		expect(verification.verified_file_count).toBe(verification.lock.files.length);
	});

	it("rejects a wrong expected identity and modified runtime bytes", () => {
		expect(() =>
			verifyPortableRuntime(generatedRuntime, { expected_runtime_digest: `sha256:${"0".repeat(64)}` }),
		).toThrow(/expected distribution digest/);
		const copied = copiedRuntime();
		try {
			const marker = join(copied.root, ".sure-generated");
			writeFileSync(marker, `${readFileSync(marker, "utf8")}tampered\n`);
			expect(() => verifyPortableRuntime(copied.root)).toThrow(/file digest mismatch/);
		} finally {
			rmSync(copied.parent, { recursive: true, force: true });
		}
	});

	it("rejects unexpected files and symlinks", () => {
		const withExtra = copiedRuntime();
		try {
			writeFileSync(join(withExtra.root, "unexpected.py"), "raise SystemExit(0)\n");
			expect(() => verifyPortableRuntime(withExtra.root)).toThrow(/file set/);
		} finally {
			rmSync(withExtra.parent, { recursive: true, force: true });
		}
		const withSymlink = copiedRuntime();
		try {
			symlinkSync(join(withSymlink.root, "semantic-backends.json"), join(withSymlink.root, "injected.json"));
			expect(() => verifyPortableRuntime(withSymlink.root)).toThrow(PortableRuntimeVerificationError);
			expect(() => verifyPortableRuntime(withSymlink.root)).toThrow(/symlink/);
		} finally {
			rmSync(withSymlink.parent, { recursive: true, force: true });
		}
	});
});
