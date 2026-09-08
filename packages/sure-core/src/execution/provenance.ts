import { createHash } from "node:crypto";
import { canonicalJsonDigest } from "../contracts/canonical-json.ts";
import type { ExecutionReceipt, ExecutionRequest, JsonValue } from "../contracts/types.ts";
import type { RunStoreLock } from "../run/types.ts";
import {
	EXECUTION_COMPATIBILITY_SCHEMA,
	type ExecutionContractBundle,
	type ExecutionContractBundleOptions,
	type ExecutionContractHistory,
	type ExecutionContractHistoryValidation,
	executionContractHistoryDigest,
	validateExecutionContractBundle,
	validateExecutionContractHistory,
} from "./bundle.ts";
import type { ExecutionAdmissionTrace } from "./types.ts";

const REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;

export const EXECUTION_PROVENANCE_DOCUMENTS = ["request", "receipt", "admission", "contract"] as const;
export type ExecutionProvenanceDocument = (typeof EXECUTION_PROVENANCE_DOCUMENTS)[number];
export type ExecutionProvenanceView = "latest" | "immutable";

export interface ExecutionProvenanceDocumentKey {
	view: ExecutionProvenanceView;
	request_id: string;
	document: ExecutionProvenanceDocument;
}

/**
 * Host-owned persistence port. Core supplies exact bytes and object keys; an
 * adapter maps those keys to a filesystem, CAS, database, or trusted store.
 */
export interface ExecutionProvenancePublicationPort {
	readonly lock: RunStoreLock;
	location(key: ExecutionProvenanceDocumentKey): string;
	read(key: ExecutionProvenanceDocumentKey): string | undefined;
	fileType(key: ExecutionProvenanceDocumentKey): "missing" | "file" | "directory" | "symlink" | "other";
	digest(key: ExecutionProvenanceDocumentKey): string | undefined;
	writeLatest(key: ExecutionProvenanceDocumentKey, content: string): void;
	writeImmutable(key: ExecutionProvenanceDocumentKey, content: string): void;
}

export interface PublishedExecutionDocument {
	path: string;
	digest: string;
	canonical_digest: string;
}

export interface PublishedExecutionDocumentPair {
	latest: PublishedExecutionDocument;
	immutable: PublishedExecutionDocument;
}

export interface PublishedExecutionRequest {
	request_id: string;
	request_digest: string;
	documents: PublishedExecutionDocumentPair;
}

export interface ExecutionProvenanceLegacyViews {
	execution_surface?: string | null;
	execution_result?: string | null;
}

export interface PublishExecutionCompletionInput {
	request: ExecutionRequest;
	receipt?: ExecutionReceipt;
	admission: ExecutionAdmissionTrace;
	validation_options?: ExecutionContractBundleOptions;
	legacy_views?: ExecutionProvenanceLegacyViews;
}

export interface PublishedExecutionProvenance {
	request_id: string;
	history: ExecutionContractHistory;
	history_digest: string;
	validation: ExecutionContractHistoryValidation;
	documents: {
		request: PublishedExecutionDocumentPair;
		receipt?: PublishedExecutionDocumentPair;
		admission: PublishedExecutionDocumentPair;
		contract: PublishedExecutionDocumentPair;
	};
}

export class ExecutionProvenancePublicationError extends Error {
	readonly code = "SURE_EXECUTION_PROVENANCE_PUBLICATION_FAILED";
}

function key(
	requestId: string,
	view: ExecutionProvenanceView,
	document: ExecutionProvenanceDocument,
): ExecutionProvenanceDocumentKey {
	return { view, request_id: requestId, document };
}

function json(value: unknown): string {
	return `${JSON.stringify(value, null, 2)}\n`;
}

function rawDigest(content: string): string {
	return `sha256:${createHash("sha256").update(content).digest("hex")}`;
}

function sameJson(left: unknown, right: unknown): boolean {
	return canonicalJsonDigest(left as JsonValue) === canonicalJsonDigest(right as JsonValue);
}

function assertRequestId(request: ExecutionRequest): string {
	if (request.schema !== "sure.execution_request.v1") {
		throw new ExecutionProvenancePublicationError("execution provenance requires an execution_request.v1 document");
	}
	if (typeof request.request_id !== "string" || !REQUEST_ID.test(request.request_id)) {
		throw new ExecutionProvenancePublicationError("execution provenance request_id is unsafe");
	}
	return request.request_id;
}

function allKeys(requestId: string): ExecutionProvenanceDocumentKey[] {
	return (["latest", "immutable"] as const).flatMap((view) =>
		EXECUTION_PROVENANCE_DOCUMENTS.map((document) => key(requestId, view, document)),
	);
}

function assertLocations(port: ExecutionProvenancePublicationPort, requestId: string): void {
	const locations = allKeys(requestId).map((entry) => port.location(entry));
	if (locations.some((location) => typeof location !== "string" || location.trim() === "")) {
		throw new ExecutionProvenancePublicationError("execution provenance adapter returned an empty location");
	}
	if (new Set(locations).size !== locations.length) {
		throw new ExecutionProvenancePublicationError("execution provenance adapter aliased distinct documents");
	}
}

function parseDocument(content: string, location: string): Record<string, unknown> {
	let value: unknown;
	try {
		value = JSON.parse(content) as unknown;
	} catch (error) {
		throw new ExecutionProvenancePublicationError(
			`execution provenance document is not JSON at ${location}: ${error instanceof Error ? error.message : String(error)}`,
		);
	}
	if (typeof value !== "object" || value === null || Array.isArray(value)) {
		throw new ExecutionProvenancePublicationError(`execution provenance document is not an object at ${location}`);
	}
	return value as Record<string, unknown>;
}

function readExpected(
	port: ExecutionProvenancePublicationPort,
	documentKey: ExecutionProvenanceDocumentKey,
	expected: unknown,
): { value: Record<string, unknown>; reference: PublishedExecutionDocument } {
	const location = port.location(documentKey);
	if (port.fileType(documentKey) !== "file") {
		throw new ExecutionProvenancePublicationError(`execution provenance document is not a regular file: ${location}`);
	}
	const content = port.read(documentKey);
	if (content === undefined) {
		throw new ExecutionProvenancePublicationError(`execution provenance document disappeared: ${location}`);
	}
	const expectedContent = json(expected);
	if (content !== expectedContent) {
		throw new ExecutionProvenancePublicationError(
			`execution provenance bytes changed during publication: ${location}`,
		);
	}
	const value = parseDocument(content, location);
	if (!sameJson(value, expected)) {
		throw new ExecutionProvenancePublicationError(
			`execution provenance value changed during publication: ${location}`,
		);
	}
	const digest = port.digest(documentKey);
	const expectedDigest = rawDigest(content);
	if (digest === undefined || !DIGEST.test(digest) || digest !== expectedDigest) {
		throw new ExecutionProvenancePublicationError(`execution provenance digest is invalid at ${location}`);
	}
	return {
		value,
		reference: {
			path: location,
			digest,
			canonical_digest: canonicalJsonDigest(value as JsonValue),
		},
	};
}

function assertAbsent(port: ExecutionProvenancePublicationPort, documentKey: ExecutionProvenanceDocumentKey): void {
	if (port.fileType(documentKey) !== "missing") {
		throw new ExecutionProvenancePublicationError(
			`execution provenance contains an unexpected ${documentKey.document}: ${port.location(documentKey)}`,
		);
	}
}

function assertLatestRequestSlot(
	port: ExecutionProvenancePublicationPort,
	documentKey: ExecutionProvenanceDocumentKey,
	request: ExecutionRequest,
): void {
	const type = port.fileType(documentKey);
	if (type === "missing") return;
	const location = port.location(documentKey);
	if (type !== "file") {
		throw new ExecutionProvenancePublicationError(
			`execution provenance request slot is not a regular file: ${location}`,
		);
	}
	const content = port.read(documentKey);
	if (content === json(request)) return;
	if (content !== undefined) {
		try {
			const existing = JSON.parse(content) as unknown;
			if (
				typeof existing === "object" &&
				existing !== null &&
				!Array.isArray(existing) &&
				sameJson(existing, request)
			) {
				return;
			}
		} catch {
			// The stable refusal below covers malformed and semantically different slots.
		}
	}
	throw new ExecutionProvenancePublicationError(
		`refusing to replace an execution provenance request already published at ${location}`,
	);
}

function locationSet(port: ExecutionProvenancePublicationPort, requestId: string) {
	const location = (view: ExecutionProvenanceView, document: ExecutionProvenanceDocument) =>
		port.location(key(requestId, view, document));
	return {
		latest: {
			request: location("latest", "request"),
			receipt: location("latest", "receipt"),
			admission: location("latest", "admission"),
			contract: location("latest", "contract"),
		},
		immutable: {
			request: location("immutable", "request"),
			receipt: location("immutable", "receipt"),
			admission: location("immutable", "admission"),
			contract: location("immutable", "contract"),
		},
	};
}

function contractRecords(input: PublishExecutionCompletionInput, port: ExecutionProvenancePublicationPort) {
	const requestId = input.request.request_id;
	const locations = locationSet(port, requestId);
	const auditOptions: ExecutionContractBundleOptions = {
		...(input.validation_options ?? {}),
		require_admission: true,
		require_contract_record: false,
		accept_legacy_uninstrumented: false,
	};
	const base: ExecutionContractBundle = {
		request: input.request,
		...(input.receipt === undefined ? {} : { receipt: input.receipt }),
		admission: input.admission,
	};
	const baseValidation = validateExecutionContractBundle(base, auditOptions);
	const historyLocations = {
		request_path: locations.immutable.request,
		receipt_path: input.receipt === undefined ? null : locations.immutable.receipt,
		admission_path: locations.immutable.admission,
		contract_path: locations.immutable.contract,
	};
	const common = {
		schema: EXECUTION_COMPATIBILITY_SCHEMA,
		version: 1,
		request_digest: canonicalJsonDigest(input.request as unknown as JsonValue),
		...(input.receipt === undefined
			? {}
			: {
					receipt_digest: canonicalJsonDigest(input.receipt as unknown as JsonValue),
					lifecycle: input.receipt.lifecycle,
				}),
		admission_digest: canonicalJsonDigest(input.admission as unknown as JsonValue),
		admission_instrumentation: "admission-v1",
		contract_valid: baseValidation.valid,
		diagnostics: [...baseValidation.errors],
		legacy_views: {
			execution_surface: input.legacy_views?.execution_surface ?? null,
			execution_result: input.legacy_views?.execution_result ?? null,
		},
		history: historyLocations,
	};
	return {
		latest: {
			...common,
			request_path: locations.latest.request,
			...(input.receipt === undefined ? {} : { receipt_path: locations.latest.receipt }),
			admission_path: locations.latest.admission,
		},
		immutable: {
			...common,
			request_path: locations.immutable.request,
			...(input.receipt === undefined ? {} : { receipt_path: locations.immutable.receipt }),
			admission_path: locations.immutable.admission,
		},
	};
}

/**
 * Core-owned publication protocol. It never advances workflow state and never
 * upgrades assurance; host policy decides how strongly the injected port is
 * protected from the agent.
 */
export class ExecutionProvenancePublisher {
	private readonly port: ExecutionProvenancePublicationPort;

	constructor(port: ExecutionProvenancePublicationPort) {
		this.port = port;
	}

	publishRequest(request: ExecutionRequest): PublishedExecutionRequest {
		const requestId = assertRequestId(request);
		assertLocations(this.port, requestId);
		return this.port.lock.withLock(`execution-provenance:${requestId}`, () => {
			const latestKey = key(requestId, "latest", "request");
			const immutableKey = key(requestId, "immutable", "request");
			const content = json(request);
			assertLatestRequestSlot(this.port, latestKey, request);
			this.port.writeImmutable(immutableKey, content);
			this.port.writeLatest(latestKey, content);
			const latest = readExpected(this.port, latestKey, request);
			const immutable = readExpected(this.port, immutableKey, request);
			return {
				request_id: requestId,
				request_digest: canonicalJsonDigest(request as unknown as JsonValue),
				documents: { latest: latest.reference, immutable: immutable.reference },
			};
		});
	}

	publishCompletion(input: PublishExecutionCompletionInput): PublishedExecutionProvenance {
		const requestId = assertRequestId(input.request);
		assertLocations(this.port, requestId);
		return this.port.lock.withLock(`execution-provenance:${requestId}`, () => {
			const latestRequestKey = key(requestId, "latest", "request");
			const immutableRequestKey = key(requestId, "immutable", "request");
			const latestRequest = readExpected(this.port, latestRequestKey, input.request);
			const immutableRequest = readExpected(this.port, immutableRequestKey, input.request);
			const records = contractRecords(input, this.port);
			const plannedHistory: ExecutionContractHistory = {
				latest: {
					request: input.request,
					...(input.receipt === undefined ? {} : { receipt: input.receipt }),
					admission: input.admission,
					contract: records.latest,
				},
				immutable: {
					request: input.request,
					...(input.receipt === undefined ? {} : { receipt: input.receipt }),
					admission: input.admission,
					contract: records.immutable,
				},
			};

			const immutableAdmissionKey = key(requestId, "immutable", "admission");
			const immutableReceiptKey = key(requestId, "immutable", "receipt");
			const immutableContractKey = key(requestId, "immutable", "contract");
			this.port.writeImmutable(immutableAdmissionKey, json(input.admission));
			if (input.receipt === undefined) assertAbsent(this.port, immutableReceiptKey);
			else this.port.writeImmutable(immutableReceiptKey, json(input.receipt));
			this.port.writeImmutable(immutableContractKey, json(records.immutable));

			const latestAdmissionKey = key(requestId, "latest", "admission");
			const latestReceiptKey = key(requestId, "latest", "receipt");
			const latestContractKey = key(requestId, "latest", "contract");
			this.port.writeLatest(latestRequestKey, json(input.request));
			this.port.writeLatest(latestAdmissionKey, json(input.admission));
			if (input.receipt === undefined) assertAbsent(this.port, latestReceiptKey);
			else this.port.writeLatest(latestReceiptKey, json(input.receipt));
			this.port.writeLatest(latestContractKey, json(records.latest));

			const latestAdmission = readExpected(this.port, latestAdmissionKey, input.admission);
			const immutableAdmission = readExpected(this.port, immutableAdmissionKey, input.admission);
			const latestContract = readExpected(this.port, latestContractKey, records.latest);
			const immutableContract = readExpected(this.port, immutableContractKey, records.immutable);
			const latestReceipt =
				input.receipt === undefined ? undefined : readExpected(this.port, latestReceiptKey, input.receipt);
			const immutableReceipt =
				input.receipt === undefined ? undefined : readExpected(this.port, immutableReceiptKey, input.receipt);
			const history: ExecutionContractHistory = {
				latest: {
					request: latestRequest.value as unknown as ExecutionRequest,
					...(latestReceipt === undefined ? {} : { receipt: latestReceipt.value as unknown as ExecutionReceipt }),
					admission: latestAdmission.value as unknown as ExecutionAdmissionTrace,
					contract: latestContract.value,
				},
				immutable: {
					request: immutableRequest.value as unknown as ExecutionRequest,
					...(immutableReceipt === undefined
						? {}
						: { receipt: immutableReceipt.value as unknown as ExecutionReceipt }),
					admission: immutableAdmission.value as unknown as ExecutionAdmissionTrace,
					contract: immutableContract.value,
				},
			};
			const plannedDigest = executionContractHistoryDigest(plannedHistory);
			const historyDigest = executionContractHistoryDigest(history);
			if (historyDigest !== plannedDigest) {
				throw new ExecutionProvenancePublicationError("execution provenance changed while reloading its history");
			}
			const validation = validateExecutionContractHistory(history, {
				...(input.validation_options ?? {}),
				require_admission: true,
				require_contract_record: true,
				accept_legacy_uninstrumented: false,
			});
			return {
				request_id: requestId,
				history,
				history_digest: historyDigest,
				validation,
				documents: {
					request: { latest: latestRequest.reference, immutable: immutableRequest.reference },
					...(latestReceipt === undefined || immutableReceipt === undefined
						? {}
						: { receipt: { latest: latestReceipt.reference, immutable: immutableReceipt.reference } }),
					admission: { latest: latestAdmission.reference, immutable: immutableAdmission.reference },
					contract: { latest: latestContract.reference, immutable: immutableContract.reference },
				},
			};
		});
	}
}
