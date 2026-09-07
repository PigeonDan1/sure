import { describe, expect, it } from "vitest";
import { parseDockerRuntimeRequirements } from "../src/index.ts";

const IMAGE = `registry.example/sure/trans@sha256:${"a".repeat(64)}`;

describe("Docker execution request contract", () => {
	it("normalizes a bounded image, mount, environment, and workdir", () => {
		const result = parseDockerRuntimeRequirements({
			docker_image: IMAGE,
			docker_mounts: [{ source: "/tmp/sure-run", target: "/work", read_only: true }],
			docker_env: { SURE_MODE: "check" },
			docker_workdir: "/work",
			docker_network: "none",
			docker_image_digest: `sha256:${"a".repeat(64)}`,
		});

		expect(result).toEqual({
			valid: true,
			errors: [],
			spec: {
				image: IMAGE,
				mounts: [{ source: "/tmp/sure-run", target: "/work", read_only: true }],
				environment: { SURE_MODE: "check" },
				working_directory: "/work",
				network: "none",
				image_digest: `sha256:${"a".repeat(64)}`,
			},
		});
	});

	it("rejects non-absolute or escaping container paths and invalid env values", () => {
		const result = parseDockerRuntimeRequirements({
			docker_image: IMAGE,
			docker_mounts: [{ source: "relative", target: "/work/../escape", read_only: false }],
			docker_env: { "BAD-KEY": 1 },
			docker_workdir: "relative",
		});

		expect(result.valid).toBe(false);
		expect(result.errors).toEqual(
			expect.arrayContaining([
				"runtime_requirements.docker_mounts[0].target must be an absolute non-escaping POSIX path",
				"runtime_requirements.docker_env key is invalid: BAD-KEY",
				"runtime_requirements.docker_env.BAD-KEY must be a string",
				"runtime_requirements.docker_workdir must be an absolute non-escaping POSIX path",
			]),
		);
	});

	it("requires an image and rejects malformed mounts", () => {
		const result = parseDockerRuntimeRequirements({
			docker_mounts: [{ source: "/tmp/sure-run", target: "/work", read_only: "false" }],
		});

		expect(result.valid).toBe(false);
		expect(result.errors).toEqual(
			expect.arrayContaining([
				"runtime_requirements.docker_image must be a non-empty string",
				"runtime_requirements.docker_mounts[0].read_only must be boolean",
			]),
		);
	});

	it("requires an explicit mount list even when the image is valid", () => {
		const result = parseDockerRuntimeRequirements({ docker_image: IMAGE });

		expect(result.valid).toBe(false);
		expect(result.errors).toContain("runtime_requirements.docker_mounts must be an array");
	});
});
