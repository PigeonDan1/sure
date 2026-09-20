#!/usr/bin/env node

import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { validateGeneratedModelData } from "./model-data.ts";

const packageRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

try {
	validateGeneratedModelData(packageRoot);
	console.log("Generated model data is valid.");
} catch (error) {
	console.error(error instanceof Error ? error.message : String(error));
	console.error(
		"\nModel data is missing or stale. The catalog under src/providers/data/ is frozen and hand-maintained: restore it with git, or, if the edit was intentional, update the file hash and structureHash in src/providers/data/.manifest.json and the provider imports in src/models.generated.ts to match.",
	);
	process.exitCode = 1;
}
