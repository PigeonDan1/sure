import { repoRootForPackage } from "../harness/resolve.ts";
import { MemoryService } from "./service.ts";

/**
 * Pi-only construction bridge for the host-neutral memory service.
 *
 * The package-directory convention is an adapter concern. Keeping it here
 * means the shared memory orchestration receives explicit roots and can be
 * reused by a non-Pi host without importing Pi lifecycle types or resolver
 * conventions.
 */
export function memoryServiceForPackage(packageDir: string): MemoryService {
	return MemoryService.fromRepoRoot(repoRootForPackage(packageDir));
}
