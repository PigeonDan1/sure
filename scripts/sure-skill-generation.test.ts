import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { canonicalJson, canonicalJsonDigest } from "../packages/sure-core/src/contracts/canonical-json.ts";
import type { JsonValue } from "../packages/sure-core/src/contracts/types.ts";
import { CANONICAL_MEMORY_CONTRACT } from "../sure/canonical/shared/memory-contract.ts";
import { CANONICAL_SKILLS } from "../sure/canonical/skills/index.ts";
import { canonicalValidatorRegistry } from "../sure/canonical/validators/index.ts";

const repositoryRoot = join(fileURLToPath(new URL("..", import.meta.url)));

function readJson(path: string): Record<string, unknown> {
	return JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
}

function asJson(value: unknown): JsonValue {
	return value as JsonValue;
}

describe("canonical SURE skill generation", () => {
	it("has one stable command mapping per skill and matching host workflow digests", () => {
		const canonicalRegistry = readJson(join(repositoryRoot, "sure/canonical/validators/registry.json"));
		expect(CANONICAL_SKILLS).toHaveLength(6);
		expect(new Set(CANONICAL_SKILLS.map((skill) => skill.command_id)).size).toBe(6);
		for (const skill of CANONICAL_SKILLS) {
			const piLock = readJson(
				join(repositoryRoot, "sure/generated/pi/skills", skill.skill_id, "generation.lock.json"),
			);
			const portableLock = readJson(
				join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "generation.lock.json"),
			);
			expect(piLock.workflow_digest).toBe(portableLock.workflow_digest);
			expect(piLock.definition_digest).toBe(portableLock.definition_digest);
			expect(piLock.semantic_backend_digest).toBe(portableLock.semantic_backend_digest);
			expect(piLock.validator_registry_digest).toBe(canonicalRegistry.digest);
			expect(portableLock.validator_registry_digest).toBe(piLock.validator_registry_digest);
			expect(piLock.workflow_digest).toBe(canonicalJsonDigest(asJson(skill.workflow)));
		}
	});

	it("ships the same validator identities to both hosts", () => {
		const canonicalFile = readJson(join(repositoryRoot, "sure/canonical/validators/registry.json"));
		const declared = canonicalValidatorRegistry().snapshot();
		expect(canonicalFile.schema).toBe(declared.schema);
		const materialized = canonicalFile.validators as Array<Record<string, unknown>>;
		expect(materialized.map((descriptor) => descriptor.id)).toEqual(
			declared.validators.map((descriptor) => descriptor.id),
		);
		for (const descriptor of materialized) {
			if (typeof descriptor.resource_path !== "string") continue;
			const content = readFileSync(join(repositoryRoot, "sure/canonical/skills", descriptor.resource_path));
			expect(descriptor.resource_digest).toBe(`sha256:${createHash("sha256").update(content).digest("hex")}`);
		}
		for (const skill of CANONICAL_SKILLS) {
			const pi = readJson(
				join(repositoryRoot, "sure/generated/pi/skills", skill.skill_id, "validator-registry.json"),
			);
			const portable = readJson(
				join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "validator-registry.json"),
			);
			expect(pi).toEqual(portable);
			expect(pi.schema).toBe("sure.validator.registry.v1");
			expect(pi.digest).toBe(canonicalFile.digest);
		}
	});

	it("associates auxiliary validators with their owning workflow unit", () => {
		const transDescriptors = canonicalValidatorRegistry()
			.snapshot()
			.validators.filter(
				(descriptor) =>
					descriptor.skill_id === "sure_trans" &&
					descriptor.branch_id === "main" &&
					descriptor.unit_id === "generate_adapter",
			);
		expect(transDescriptors.map((descriptor) => descriptor.legacy_id)).toEqual([
			"python-script",
			"legacy-in-process",
		]);
		expect(transDescriptors.map((descriptor) => descriptor.id)).toEqual([
			"sure.sure_trans.main.generate_adapter",
			"sure.sure_trans.main.generate_adapter_aux_legacy_in_process",
		]);
		expect(
			transDescriptors.every(
				(descriptor) => descriptor.input_contract === "workflow:sure_trans/main/generate_adapter",
			),
		).toBe(true);
	});

	it("projects the legacy Pi manifest without changing its public fields", () => {
		for (const skill of CANONICAL_SKILLS) {
			const generated = readJson(
				join(repositoryRoot, "sure/generated/pi/skills", skill.skill_id, "sure.skill.json"),
			);
			const legacy = readJson(join(repositoryRoot, "sure/skills", skill.skill_id, "sure.skill.json"));
			expect(canonicalJson(asJson(generated))).toBe(canonicalJson(asJson(legacy)));
		}
	});

	it("keeps portable packages host-neutral and frontmatter-addressable", () => {
		const forbidden =
			/@earendil-works\/pi-coding-agent|HARNESS_PYTHON_BIN|sure_finish|sure_update_state|pre_start|pre_tool_call|post_tool_result|\.\.\/sure_[a-z_]+|sure\/skills\/sure_/;
		for (const skill of CANONICAL_SKILLS) {
			const root = join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug);
			const markdown = readFileSync(join(root, "SKILL.md"), "utf8");
			expect(markdown).toMatch(/^---\nname: [a-z0-9-]+\ndescription: /);
			expect(markdown).not.toMatch(forbidden);
			expect(readFileSync(join(root, "agents/openai.yaml"), "utf8")).toContain(`$${skill.distribution_slug}`);
			expect(readFileSync(join(root, "canonical-definition.json"), "utf8")).not.toMatch(forbidden);
		}
	});

	it("ships one memory contract projection to both hosts", () => {
		const canonical = readJson(join(repositoryRoot, "sure/canonical/shared/memory-contract.json"));
		expect(canonical).toEqual(CANONICAL_MEMORY_CONTRACT);
		for (const skill of CANONICAL_SKILLS) {
			const pi = readJson(join(repositoryRoot, "sure/generated/pi/skills", skill.skill_id, "memory-contract.json"));
			const portable = readJson(
				join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "memory-contract.json"),
			);
			expect(pi).toEqual(portable);
			const { skill: projection, ...base } = pi;
			expect(base).toEqual(canonical);
			expect(projection).toEqual({
				skill_id: skill.skill_id,
				enabled: skill.skill_id !== "sure_approve",
				participates_in_checkpoint: skill.skill_id !== "sure_approve",
			});
			const piLock = readJson(
				join(repositoryRoot, "sure/generated/pi/skills", skill.skill_id, "generation.lock.json"),
			);
			expect(piLock.memory_contract_digest).toBe(canonicalJsonDigest(asJson(pi)));
			expect(
				readFileSync(
					join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "memory-contract.json"),
					"utf8",
				),
			).not.toMatch(/@earendil-works\/pi-coding-agent|sure\/skills\/sure_/);
		}
	});

	it("ships a Pi-free portable memory entrypoint description", () => {
		for (const skill of CANONICAL_SKILLS) {
			const readme = readFileSync(
				join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "memory", "README.md"),
				"utf8",
			);
			expect(readme).toContain("surectl memory --contract ./memory-contract.json");
			expect(readme).toContain(`--skill ${skill.skill_id}`);
			expect(readme).not.toMatch(/@earendil-works\/pi-coding-agent|sure\/skills\/sure_|HARNESS_PYTHON_BIN/);
		}
	});

	it("is reproducible and does not require a production reference root", () => {
		const script = join(repositoryRoot, "scripts/generate-sure-skills.ts");
		execFileSync(process.execPath, ["--import", "tsx", script, "--check"], { cwd: repositoryRoot, stdio: "pipe" });
		for (const skill of CANONICAL_SKILLS) {
			expect(
				existsSync(join(repositoryRoot, "sure/dist/agent-skills", skill.distribution_slug, "generation.lock.json")),
			).toBe(true);
		}
		expect(readFileSync(join(repositoryRoot, "sure/dist/agent-skills/registry.json"), "utf8")).not.toContain(
			"/hpc_stor03",
		);
	});
});
