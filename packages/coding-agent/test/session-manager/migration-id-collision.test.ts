import { beforeEach, describe, expect, it, vi } from "vitest";
import {
	buildContextEntries,
	type FileEntry,
	migrateSessionEntries,
	type SessionEntry,
} from "../../src/core/session-manager.ts";

type Uuid = `${string}-${string}-${string}-${string}-${string}`;

const mocked = vi.hoisted(() => ({ uuids: [] as Uuid[] }));

// Entry ids are the first 8 hex chars of a random UUID, so a collision cannot be
// produced by chance in a test. Feed the generator a fixed sequence instead.
vi.mock("crypto", async (importOriginal) => {
	const actual = await importOriginal<typeof import("crypto")>();
	return { ...actual, randomUUID: () => mocked.uuids.shift() ?? actual.randomUUID() };
});

const DUPLICATE: Uuid = "dupdupdu-0000-4000-8000-000000000000";
const DISTINCT: Uuid = "distinct-0000-4000-8000-000000000000";

function userMessage(text: string, timestamp: number): Record<string, unknown> {
	return {
		type: "message",
		timestamp: `2025-01-01T00:00:0${timestamp}Z`,
		message: { role: "user", content: text, timestamp },
	};
}

describe("v1 to v2 migration id collisions", () => {
	beforeEach(() => {
		mocked.uuids.length = 0;
	});

	it("gives every migrated entry a distinct id", () => {
		mocked.uuids.push(DUPLICATE, DUPLICATE, DISTINCT);
		const entries = [
			{ type: "session", id: "sess-1", timestamp: "2025-01-01T00:00:00Z", cwd: "/tmp" },
			userMessage("one", 1),
			userMessage("two", 2),
		] as unknown as FileEntry[];

		migrateSessionEntries(entries);

		const first = entries[1] as unknown as SessionEntry;
		const second = entries[2] as unknown as SessionEntry;
		expect(first.id).toBe("dupdupdu");
		expect(second.id).not.toBe(first.id);
		// A self-referencing parent pointer is what turns a collision into a cycle.
		expect(second.parentId).toBe(first.id);
	});

	it("stops walking parent pointers that form a cycle", () => {
		// What a session file already carrying duplicate ids looks like once loaded.
		const first = { ...userMessage("one", 1), id: "dupdupdu", parentId: null } as unknown as SessionEntry;
		const second = { ...userMessage("two", 2), id: "dupdupdu", parentId: "dupdupdu" } as unknown as SessionEntry;
		const entries = [first, second];

		// A plain Map would let an unbounded walk spin until the process dies, which
		// no test timeout can interrupt: the walk is synchronous. Fail fast instead.
		class BoundedIndex extends Map<string, SessionEntry> {
			private lookups = 0;
			override get(id: string): SessionEntry | undefined {
				if (++this.lookups > 100) {
					throw new Error("parent walk did not terminate on a cyclic session");
				}
				return super.get(id);
			}
		}
		const byId = new BoundedIndex();
		for (const entry of entries) byId.set(entry.id, entry);

		const path = buildContextEntries(entries, "dupdupdu", byId);

		expect(path.length).toBeLessThanOrEqual(entries.length);
	});
});
