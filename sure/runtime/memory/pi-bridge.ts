import { repoRootForPackage } from "../harness/resolve.ts";
import { type MemoryRunContext, MemoryService } from "./service.ts";

export type PiMemoryRootOverrides = Partial<
	Pick<MemoryRunContext, "repoRoot" | "memoryRoot" | "canonicalRoot" | "legacySkillsRoot">
>;

/**
 * Pi-only construction bridge for the host-neutral memory service.
 *
 * The package-directory convention is an adapter concern. Keeping it here
 * means the shared memory orchestration receives explicit roots and can be
 * reused by a non-Pi host without importing Pi lifecycle types or resolver
 * conventions.
 */
export function memoryServiceForPackage(packageDir: string, overrides: PiMemoryRootOverrides = {}): MemoryService {
	return MemoryService.fromRepoRoot(overrides.repoRoot ?? repoRootForPackage(packageDir), {
		...(overrides.memoryRoot === undefined ? {} : { memoryRoot: overrides.memoryRoot }),
		...(overrides.canonicalRoot === undefined ? {} : { canonicalRoot: overrides.canonicalRoot }),
		...(overrides.legacySkillsRoot === undefined ? {} : { legacySkillsRoot: overrides.legacySkillsRoot }),
	});
}

/** Resolve a Pi hook context using explicit roots when the host supplied them. */
export function memoryServiceForContext(context: MemoryRunContext): MemoryService {
	return context.repoRoot ? MemoryService.fromRunContext(context) : memoryServiceForPackage(context.packageDir);
}
