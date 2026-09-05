import { createHash } from "node:crypto";
import type { JsonValue } from "./types.ts";

function compareUtf16(left: string, right: string): number {
	return left < right ? -1 : left > right ? 1 : 0;
}

function canonicalize(value: unknown, active: WeakSet<object>): string {
	if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
	if (typeof value === "number") {
		if (!Number.isFinite(value)) throw new TypeError("Canonical JSON does not permit non-finite numbers");
		return JSON.stringify(value);
	}
	if (typeof value !== "object") {
		throw new TypeError(`Canonical JSON does not permit ${typeof value} values`);
	}
	if (active.has(value)) throw new TypeError("Canonical JSON does not permit cyclic values");
	active.add(value);
	try {
		if (Array.isArray(value)) {
			for (let index = 0; index < value.length; index++) {
				if (!(index in value)) throw new TypeError("Canonical JSON does not permit sparse arrays");
			}
			return `[${value.map((entry) => canonicalize(entry, active)).join(",")}]`;
		}

		const prototype = Object.getPrototypeOf(value);
		if (prototype !== Object.prototype && prototype !== null) {
			throw new TypeError("Canonical JSON only accepts plain objects");
		}
		if (Object.getOwnPropertySymbols(value).length > 0) {
			throw new TypeError("Canonical JSON does not permit symbol keys");
		}
		const record = value as Record<string, unknown>;
		const entries = Object.keys(record)
			.sort(compareUtf16)
			.map((key) => `${JSON.stringify(key)}:${canonicalize(record[key], active)}`);
		return `{${entries.join(",")}}`;
	} finally {
		active.delete(value);
	}
}

export function canonicalJson(value: JsonValue): string {
	return canonicalize(value, new WeakSet<object>());
}

export function sha256Hex(value: string | Uint8Array): string {
	return createHash("sha256").update(value).digest("hex");
}

export function canonicalJsonSha256(value: JsonValue): string {
	return sha256Hex(canonicalJson(value));
}

export function canonicalJsonDigest(value: JsonValue): string {
	return `sha256:${canonicalJsonSha256(value)}`;
}
