import { isAbsolute, join } from "node:path";
import { ExecutionProvenancePublisher } from "@earendil-works/sure-core";
import { NodeExecutionProvenancePublicationPort } from "@earendil-works/sure-core/node";
import type { SureRunRecord } from "./types.ts";

const SAFE_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export interface PiExecutionProvenancePublisherOptions {
	run: Pick<SureRunRecord, "runDir" | "outputDir">;
	unit_id: string;
	invocation_id: string;
	forbidden_output_roots?: readonly string[];
}

export interface PiExecutionProvenancePublisher {
	root: string;
	publisher: ExecutionProvenancePublisher;
}

/**
 * Pi-owned location adapter for the shared publication protocol. This factory
 * grants no assurance by itself; issuer verification remains in the host-only
 * lifecycle boundary.
 */
export function createPiExecutionProvenancePublisher(
	options: PiExecutionProvenancePublisherOptions,
): PiExecutionProvenancePublisher {
	if (!isAbsolute(options.run.runDir)) throw new Error("Pi execution provenance requires an absolute runDir");
	if (!SAFE_SEGMENT.test(options.unit_id)) throw new Error("Pi execution provenance unit_id is unsafe");
	if (!SAFE_SEGMENT.test(options.invocation_id)) throw new Error("Pi execution provenance invocation_id is unsafe");
	const root = join(options.run.runDir, "artifacts", "execution", options.unit_id, options.invocation_id);
	return {
		root,
		publisher: new ExecutionProvenancePublisher(
			new NodeExecutionProvenancePublicationPort({
				root,
				allowed_roots: [options.run.runDir, ...(options.run.outputDir ? [options.run.outputDir] : [])],
				forbidden_roots: options.forbidden_output_roots ?? [],
			}),
		),
	};
}
