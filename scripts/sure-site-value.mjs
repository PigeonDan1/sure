#!/usr/bin/env node
import { requireSitePolicy } from "../sure/site/loader.ts";

const key = process.argv[2];
if (!key) {
	console.error("usage: sure-site-value <storage|datasets|execution>.<field>");
	process.exit(2);
}

try {
	const value = requireSitePolicy().policy;
	const parts = key.split(".");
	let current = value;
	for (const part of parts) {
		if (typeof current !== "object" || current === null || !(part in current)) {
			throw new Error(`site policy field not found: ${key}`);
		}
		current = current[part];
	}
	if (Array.isArray(current)) {
		for (const item of current) console.log(String(item));
	} else if (typeof current === "object" && current !== null) {
		console.log(JSON.stringify(current));
	} else {
		console.log(String(current));
	}
} catch (error) {
	console.error(error instanceof Error ? error.message : String(error));
	process.exit(1);
}
