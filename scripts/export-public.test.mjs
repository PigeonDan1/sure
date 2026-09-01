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

function yamlList(name, values) {
	return values.length === 0
		? [`${name}: []`]
		: [`${name}:`, ...values.map((value) => `  - ${JSON.stringify(value)}`)];
}

function basePolicy({ forbiddenContent = [], exceptionPaths = [] } = {}) {
	return [
		"version: 1",
		"exclude:",
		"  - private/site/**",
		...yamlList("forbidden_content", forbiddenContent),
		...yamlList("forbidden_content_exception_paths", exceptionPaths),
		"forbidden_paths: []",
		"",
	].join("\n");
}

function overlayPolicy({ forbiddenContent = [], exceptions = [] } = {}) {
	const lines = ["version: 1", ...yamlList("forbidden_content", forbiddenContent)];
	if (exceptions.length > 0) {
		lines.push("forbidden_content_exceptions:");
		for (const exception of exceptions) {
			lines.push(`  - pattern: ${JSON.stringify(exception.pattern)}`, "    paths:");
			for (const path of exception.paths) lines.push(`      - ${JSON.stringify(path)}`);
		}
	}
	lines.push("forbidden_paths: []", "");
	return lines.join("\n");
}

function exportCase(prefix, files) {
	const temporaryRoot = mkdtempSync(resolve(tmpdir(), prefix));
	const sourceRoot = resolve(temporaryRoot, "source");
	mkdirSync(sourceRoot);
	repository(sourceRoot, files);
	const output = resolve(temporaryRoot, "tree");
	return {
		temporaryRoot,
		sourceRoot,
		output,
		result: run("node", [exporter, "--output", output], sourceRoot),
	};
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

test("a repository that excludes private paths must carry deny rules", () => {
	// The overlay is the only place the private deny rules live, and it sits
	// inside the excluded tree. A missing or renamed overlay must stop the
	// export, not quietly turn the content scan off.
	const temporaryRoot = mkdtempSync(resolve(tmpdir(), "sure-public-export-unarmed-test-"));
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
			"private/site/secret.txt": "restricted-site-value\n",
			"visible.txt": "public\n",
		});
		const output = resolve(temporaryRoot, "tree");
		const exported = run("node", [exporter, "--output", output], sourceRoot);
		assert.notEqual(exported.status, 0);
		assert.match(exported.stderr, /no forbidden_content rules/);
		assert.equal(existsSync(output), false);
	} finally {
		rmSync(temporaryRoot, { recursive: true, force: true });
	}
});

test("private overlay permits an exact pattern on an exact authorized path", () => {
	const path = "sure/skills/sure_eval/scripts/evaluation.py";
	const pattern = "restricted-site-value";
	const fixture = exportCase("sure-public-export-exception-pass-", {
		"public-export.yaml": basePolicy({ exceptionPaths: [path] }),
		"private/site/public-export.overlay.yaml": overlayPolicy({
			forbiddenContent: [pattern],
			exceptions: [{ pattern, paths: [path] }],
		}),
		[path]: `contains ${pattern}\n`,
	});
	try {
		assert.equal(fixture.result.status, 0, fixture.result.stderr);
		assert.equal(readFileSync(resolve(fixture.output, path), "utf8"), `contains ${pattern}\n`);
	} finally {
		rmSync(fixture.temporaryRoot, { recursive: true, force: true });
	}
});

test("private content exception does not permit the same pattern on another path", () => {
	const path = "sure/skills/sure_eval/scripts/evaluation.py";
	const pattern = "restricted-site-value";
	const fixture = exportCase("sure-public-export-exception-scope-", {
		"public-export.yaml": basePolicy({ exceptionPaths: [path] }),
		"private/site/public-export.overlay.yaml": overlayPolicy({
			forbiddenContent: [pattern],
			exceptions: [{ pattern, paths: [path] }],
		}),
		[path]: `allowed only here: ${pattern}\n`,
		"other.txt": `still forbidden here: ${pattern}\n`,
	});
	try {
		assert.notEqual(fixture.result.status, 0);
		assert.match(fixture.result.stderr, /other[.]txt: contains forbidden public content/);
		assert.equal(existsSync(fixture.output), false);
	} finally {
		rmSync(fixture.temporaryRoot, { recursive: true, force: true });
	}
});

test("content exception paths reject globs, unknown paths, and duplicates", () => {
	const path = "sure/skills/sure_eval/scripts/evaluation.py";
	const pattern = "restricted-site-value";
	const cases = [
		{
			name: "glob",
			basePaths: ["sure/skills/sure_eval/scripts/*.py"],
			exceptionPaths: ["sure/skills/sure_eval/scripts/*.py"],
			expected: /must contain exact repository paths/,
		},
		{
			name: "unknown",
			basePaths: [path],
			exceptionPaths: ["other.txt"],
			expected: /not in the public closed set/,
		},
		{
			name: "duplicate-overlay",
			basePaths: [path],
			exceptionPaths: [path, path],
			expected: /private content exception contains duplicate path/,
		},
		{
			name: "duplicate-base",
			basePaths: [path, path],
			exceptionPaths: [path],
			expected: /contains duplicate path/,
		},
	];
	for (const item of cases) {
		const fixture = exportCase(`sure-public-export-exception-${item.name}-`, {
			"public-export.yaml": basePolicy({ exceptionPaths: item.basePaths }),
			"private/site/public-export.overlay.yaml": overlayPolicy({
				forbiddenContent: [pattern],
				exceptions: [{ pattern, paths: item.exceptionPaths }],
			}),
			[path]: `contains ${pattern}\n`,
			"other.txt": `contains ${pattern}\n`,
		});
		try {
			assert.notEqual(fixture.result.status, 0, item.name);
			assert.match(fixture.result.stderr, item.expected, item.name);
		} finally {
			rmSync(fixture.temporaryRoot, { recursive: true, force: true });
		}
	}
});

test("private content exception pattern must be declared by the overlay", () => {
	const path = "sure/skills/sure_eval/scripts/evaluation.py";
	const pattern = "restricted-site-value";
	const fixture = exportCase("sure-public-export-exception-undeclared-", {
		"public-export.yaml": basePolicy({ exceptionPaths: [path] }),
		"private/site/public-export.overlay.yaml": overlayPolicy({
			exceptions: [{ pattern, paths: [path] }],
		}),
		[path]: `contains ${pattern}\n`,
	});
	try {
		assert.notEqual(fixture.result.status, 0);
		assert.match(fixture.result.stderr, /pattern is not declared by overlay forbidden_content/);
	} finally {
		rmSync(fixture.temporaryRoot, { recursive: true, force: true });
	}
});

test("private content exception cannot waive a base forbidden-content rule", () => {
	const path = "sure/skills/sure_eval/scripts/evaluation.py";
	const pattern = "restricted-site-value";
	const fixture = exportCase("sure-public-export-exception-base-rule-", {
		"public-export.yaml": basePolicy({ forbiddenContent: [pattern], exceptionPaths: [path] }),
		"private/site/public-export.overlay.yaml": overlayPolicy({
			forbiddenContent: [pattern],
			exceptions: [{ pattern, paths: [path] }],
		}),
		[path]: `contains ${pattern}\n`,
	});
	try {
		assert.notEqual(fixture.result.status, 0);
		assert.match(fixture.result.stderr, /contains forbidden public content restricted-site-value/);
	} finally {
		rmSync(fixture.temporaryRoot, { recursive: true, force: true });
	}
});

test("unused private content exception fails closed", () => {
	const path = "sure/skills/sure_eval/scripts/evaluation.py";
	const pattern = "restricted-site-value";
	const fixture = exportCase("sure-public-export-exception-unused-", {
		"public-export.yaml": basePolicy({ exceptionPaths: [path] }),
		"private/site/public-export.overlay.yaml": overlayPolicy({
			forbiddenContent: [pattern],
			exceptions: [{ pattern, paths: [path] }],
		}),
		[path]: "contains no restricted value\n",
	});
	try {
		assert.notEqual(fixture.result.status, 0);
		assert.match(fixture.result.stderr, /private content exception did not match/);
	} finally {
		rmSync(fixture.temporaryRoot, { recursive: true, force: true });
	}
});

test("content exception path must be a regular text file", () => {
	const path = "sure/skills/sure_eval/scripts/evaluation.bin";
	const pattern = "restricted-site-value";
	const fixture = exportCase("sure-public-export-exception-binary-", {
		"public-export.yaml": basePolicy({ exceptionPaths: [path] }),
		"private/site/public-export.overlay.yaml": overlayPolicy({
			forbiddenContent: [pattern],
			exceptions: [{ pattern, paths: [path] }],
		}),
		[path]: `${pattern}\0payload`,
	});
	try {
		assert.notEqual(fixture.result.status, 0);
		assert.match(fixture.result.stderr, /must name a text file/);
	} finally {
		rmSync(fixture.temporaryRoot, { recursive: true, force: true });
	}
});

test("content exception path cannot be a symlink", () => {
	const temporaryRoot = mkdtempSync(resolve(tmpdir(), "sure-public-export-exception-symlink-"));
	const sourceRoot = resolve(temporaryRoot, "source");
	const output = resolve(temporaryRoot, "tree");
	const path = "evaluation-link";
	const pattern = "restricted-site-value";
	mkdirSync(sourceRoot);
	try {
		repository(sourceRoot, {
			"public-export.yaml": basePolicy({ exceptionPaths: [path] }),
			"private/site/public-export.overlay.yaml": overlayPolicy({
				forbiddenContent: [pattern],
				exceptions: [{ pattern, paths: [path] }],
			}),
			"target.txt": "public\n",
		});
		symlinkSync("target.txt", resolve(sourceRoot, path));
		assert.equal(run("git", ["add", "--", path], sourceRoot).status, 0);
		assert.equal(
			run("git", ["-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "symlink"], sourceRoot).status,
			0,
		);
		const exported = run("node", [exporter, "--output", output], sourceRoot);
		assert.notEqual(exported.status, 0);
		assert.match(exported.stderr, /must name a regular file/);
	} finally {
		rmSync(temporaryRoot, { recursive: true, force: true });
	}
});
