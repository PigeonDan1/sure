import assert from "node:assert/strict";
import { chmodSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

const exporter = resolve(import.meta.dirname, "export-public.mjs");

function run(command, args, cwd) {
	return spawnSync(command, args, { cwd, encoding: "utf8" });
}

function write(root, path, content) {
	const target = resolve(root, path);
	mkdirSync(dirname(target), { recursive: true });
	writeFileSync(target, content);
	return target;
}

function repository(root, files) {
	assert.equal(run("git", ["init", "--quiet"], root).status, 0);
	assert.equal(run("git", ["config", "user.name", "Public Export Test"], root).status, 0);
	assert.equal(run("git", ["config", "user.email", "public-export@example.invalid"], root).status, 0);
	for (const [path, content] of Object.entries(files)) write(root, path, content);
	const paths = Object.keys(files);
	assert.equal(run("git", ["add", "--", ...paths], root).status, 0);
	assert.equal(run("git", ["-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture"], root).status, 0);
}

test("exports a deterministic tracked projection and keeps source identity private", () => {
	const temporaryRoot = mkdtempSync(resolve(tmpdir(), "sure-public-export-test-"));
	const sourceRoot = resolve(temporaryRoot, "source");
	mkdirSync(sourceRoot);
	try {
		repository(sourceRoot, {
			".gitignore": "ignored.txt\n",
			"public-export.yaml": [
				"version: 1",
				"exclude:",
				"  - private/site/**",
				"forbidden_content: []",
				"forbidden_paths:",
				'  - "**/.env"',
				"",
			].join("\n"),
			"private/site/public-export.overlay.yaml": [
				"version: 1",
				"forbidden_content:",
				'  - "restricted-site-value"',
				"forbidden_paths: []",
				"",
			].join("\n"),
			"private/site/secret.txt": "restricted-site-value\n",
			"public-export-manifest.json": '{"schema":"stale"}\n',
			"scripts/run.sh": "#!/bin/sh\nprintf 'ok\\n'\n",
			"large.txt": "x".repeat(1024 * 1024 + 1),
			"visible.txt": "public\n",
		});
		chmodSync(resolve(sourceRoot, "scripts/run.sh"), 0o755);
		assert.equal(run("git", ["add", "--", "scripts/run.sh"], sourceRoot).status, 0);
		assert.equal(
			run("git", ["-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "executable mode"], sourceRoot).status,
			0,
		);
		symlinkSync("visible.txt", resolve(sourceRoot, "visible-link"));
		assert.equal(run("git", ["add", "--", "visible-link"], sourceRoot).status, 0);
		assert.equal(
			run("git", ["-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "symlink"], sourceRoot).status,
			0,
		);
		write(sourceRoot, "ignored.txt", "not tracked\n");

		const firstOutput = resolve(temporaryRoot, "first");
		const secondOutput = resolve(temporaryRoot, "second");
		const attestation = resolve(temporaryRoot, "private", "attestation.json");
		const first = run(
			"node",
			[exporter, "--output", firstOutput, "--private-attestation-output", attestation],
			sourceRoot,
		);
		assert.equal(first.status, 0, first.stderr);
		const second = run("node", [exporter, "--output", secondOutput], sourceRoot);
		assert.equal(second.status, 0, second.stderr);

		const firstManifestText = readFileSync(resolve(firstOutput, "public-export-manifest.json"), "utf8");
		assert.equal(firstManifestText, readFileSync(resolve(secondOutput, "public-export-manifest.json"), "utf8"));
		const manifest = JSON.parse(firstManifestText);
		assert.equal(manifest.schema, "sure.public_export_manifest.v2");
		assert.equal(manifest.projection_id, `sha256:${manifest.tree_sha256}`);
		assert.equal("source_commit" in manifest, false);
		assert.equal("source_dirty" in manifest, false);
		assert.equal(manifest.files.some((entry) => entry.path === "public-export-manifest.json"), false);
		assert.equal(existsSync(resolve(firstOutput, "private")), false);
		assert.equal(existsSync(resolve(firstOutput, "ignored.txt")), false);
		assert.equal(readFileSync(resolve(firstOutput, "large.txt"), "utf8").length, 1024 * 1024 + 1);
		assert.equal(lstatSync(resolve(firstOutput, "scripts/run.sh")).mode & 0o111, 0o111);
		assert.equal(lstatSync(resolve(firstOutput, "visible-link")).isSymbolicLink(), true);
		assert.equal(manifest.files.find((entry) => entry.path === "scripts/run.sh").mode, "100755");
		assert.equal(manifest.files.find((entry) => entry.path === "visible-link").mode, "120000");

		const privateMapping = JSON.parse(readFileSync(attestation, "utf8"));
		assert.equal(privateMapping.schema, "sure.private_public_export_attestation.v1");
		assert.equal(privateMapping.mappings.length, 1);
		assert.equal(privateMapping.mappings[0].projection_id, manifest.projection_id);
		assert.equal(
			privateMapping.mappings[0].source_commit,
			run("git", ["rev-parse", "HEAD"], sourceRoot).stdout.trim(),
		);
		assert.equal(existsSync(resolve(firstOutput, "attestation.json")), false);

		write(sourceRoot, "untracked.txt", "must make the source dirty\n");
		const dirtyOutput = resolve(temporaryRoot, "dirty");
		const dirty = run("node", [exporter, "--output", dirtyOutput], sourceRoot);
		assert.notEqual(dirty.status, 0);
		assert.match(dirty.stderr, /requires a clean working tree/);
		assert.equal(existsSync(dirtyOutput), false);
	} finally {
		rmSync(temporaryRoot, { recursive: true, force: true });
	}
});

test("private overlay blocks restricted public content", () => {
	const temporaryRoot = mkdtempSync(resolve(tmpdir(), "sure-public-export-deny-test-"));
	const sourceRoot = resolve(temporaryRoot, "source");
	mkdirSync(sourceRoot);
	try {
		repository(sourceRoot, {
			"public-export.yaml": [
				"version: 1",
				"exclude:",
				"  - private/site/**",
				"forbidden_content: []",
				"forbidden_paths: []",
				"",
			].join("\n"),
			"private/site/public-export.overlay.yaml": [
				"version: 1",
				"forbidden_content:",
				'  - "restricted-site-value"',
				"forbidden_paths: []",
				"",
			].join("\n"),
			"visible.txt": "contains restricted-site-value\n",
		});
		const output = resolve(temporaryRoot, "tree");
		const exported = run("node", [exporter, "--output", output], sourceRoot);
		assert.notEqual(exported.status, 0);
		assert.match(exported.stderr, /visible[.]txt: contains forbidden public content/);
		assert.equal(existsSync(output), false);
	} finally {
		rmSync(temporaryRoot, { recursive: true, force: true });
	}
});
