#!/usr/bin/env tsx
import { resolveSitePolicy, sitePolicyMissingMessage } from "../sure/site/loader.ts";

const json = process.argv.includes("--json");

try {
	const resolved = resolveSitePolicy();
	if (!resolved) {
		if (json) {
			console.log(JSON.stringify({ configured: false, message: sitePolicyMissingMessage() }));
		} else {
			console.log("configured: false");
			console.log(sitePolicyMissingMessage());
		}
		process.exit(0);
	}
	if (json) {
		console.log(
			JSON.stringify({
				configured: true,
				site_id: resolved.policy.site_id,
				policy_version: resolved.policy.policy_version,
				source: resolved.source,
				path: resolved.path,
				sha256: resolved.sha256,
			}),
		);
	} else {
		console.log("configured: true");
		console.log(`site_id: ${resolved.policy.site_id}`);
		console.log(`policy_version: ${resolved.policy.policy_version}`);
		console.log(`source: ${resolved.source}`);
		console.log(`path: ${resolved.path}`);
		console.log(`sha256: ${resolved.sha256}`);
	}
} catch (error) {
	console.error(error instanceof Error ? error.message : String(error));
	process.exit(1);
}
