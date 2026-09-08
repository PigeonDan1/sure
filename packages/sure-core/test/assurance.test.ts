import { describe, expect, it } from "vitest";
import {
	type AssuranceExpectedBinding,
	type AssuranceTrustAnchor,
	type AssuranceVerifierPort,
	createAssuranceAttestation,
	isVerifiedAssurance,
	validateAssuranceAttestation,
	verifyAssuranceAttestation,
} from "../src/index.ts";

const digest = (character: string) => `sha256:${character.repeat(64)}`;
const NOW = "2026-09-08T00:00:00.000Z";

const expected: AssuranceExpectedBinding = {
	assurance_profile: "pi_enforced",
	core_version: "0.80.3",
	run_id: "run-assurance",
	unit_id: "formal-evaluation",
	attempt: 1,
	request_digest: digest("1"),
	admission_digest: digest("2"),
	receipt_digest: digest("3"),
	execution_history_digest: digest("4"),
	validation_evidence_digest: digest("5"),
	subject_digest: digest("6"),
	approval_event_digest: digest("7"),
	workflow_digest: digest("8"),
	validator_digest: digest("9"),
	executor_digest: digest("a"),
	policy_digest: digest("b"),
	policy_snapshot_digest: digest("c"),
	reference_snapshot_digest: digest("d"),
	run_binding_digest: digest("e"),
};

const anchor: AssuranceTrustAnchor = {
	issuer_id: "pi-host",
	issuer_kind: "pi_harness",
	issuer_digest: digest("f"),
	maximum_profile: "pi_enforced",
	status: "active",
	proof_kinds: ["host_store"],
};

function attestation(overrides: Record<string, unknown> = {}) {
	return createAssuranceAttestation({
		attestation_id: "attestation-1",
		issuer: { issuer_id: anchor.issuer_id, kind: anchor.issuer_kind, version: "1", digest: anchor.issuer_digest },
		...expected,
		event_sequence: 1,
		issued_at: NOW,
		proof: { kind: "host_store", verification_material_id: "pi-host-store", value: "valid-proof" },
		...overrides,
	} as Parameters<typeof createAssuranceAttestation>[0]);
}

function verifier(overrides: Partial<AssuranceTrustAnchor> = {}): AssuranceVerifierPort {
	return {
		resolveTrustAnchor: () => ({ ...anchor, ...overrides }),
		verifyProof: (value) => ({ verified: value.proof.value === "valid-proof" }),
	};
}

describe("host assurance attestation", () => {
	it("returns process-local verified authority only after host proof verification", () => {
		const value = attestation();
		expect(validateAssuranceAttestation(value)).toEqual([]);
		const result = verifyAssuranceAttestation(value, expected, verifier());
		expect(result.verified).toBe(true);
		if (!result.verified) throw new Error(result.diagnostics.join("; "));
		expect(isVerifiedAssurance(result.assurance)).toBe(true);
		expect(Object.isFrozen(result.assurance)).toBe(true);
		expect(Object.isFrozen(result.assurance.attestation)).toBe(true);
		expect(Object.isFrozen(result.assurance.attestation.proof)).toBe(true);
		expect(Object.isFrozen(result.assurance.trust_anchor.proof_kinds)).toBe(true);
		expect(() => {
			(result.assurance as unknown as { profile: string }).profile = "trusted";
		}).toThrow();
		expect(isVerifiedAssurance(JSON.parse(JSON.stringify(result.assurance)))).toBe(false);
	});

	it("rejects unknown and revoked issuers", () => {
		const unknown: AssuranceVerifierPort = {
			resolveTrustAnchor: () => undefined,
			verifyProof: () => ({ verified: true }),
		};
		expect(verifyAssuranceAttestation(attestation(), expected, unknown)).toMatchObject({ verified: false });
		const revoked = verifyAssuranceAttestation(attestation(), expected, verifier({ status: "revoked" }));
		expect(revoked).toMatchObject({ verified: false });
		if (!revoked.verified) expect(revoked.diagnostics).toContain("assurance issuer is revoked");
	});

	it("rejects proof failure and profile escalation", () => {
		const badProof = attestation({
			proof: { kind: "host_store", verification_material_id: "pi-host-store", value: "forged" },
		});
		expect(verifyAssuranceAttestation(badProof, expected, verifier())).toMatchObject({ verified: false });
		const trustedExpected = { ...expected, assurance_profile: "trusted" as const };
		const elevated = attestation({
			assurance_profile: "trusted",
			issuer: { issuer_id: anchor.issuer_id, kind: "trusted_service", version: "1", digest: anchor.issuer_digest },
		});
		const result = verifyAssuranceAttestation(
			elevated,
			trustedExpected,
			verifier({ issuer_kind: "trusted_service", maximum_profile: "pi_enforced" }),
		);
		expect(result).toMatchObject({ verified: false });
		if (!result.verified) expect(result.diagnostics).toContain("assurance profile exceeds the trust anchor ceiling");
	});

	it("fails closed for malformed or throwing host verifier ports", () => {
		const malformed = verifyAssuranceAttestation(attestation(), expected, verifier({ proof_kinds: [] }));
		expect(malformed).toMatchObject({ verified: false });
		if (!malformed.verified)
			expect(malformed.diagnostics).toContain("assurance trust anchor proof_kinds are invalid");

		const resolutionFailure: AssuranceVerifierPort = {
			resolveTrustAnchor: () => {
				throw new Error("registry unavailable");
			},
			verifyProof: () => ({ verified: true }),
		};
		const unavailable = verifyAssuranceAttestation(attestation(), expected, resolutionFailure);
		expect(unavailable).toMatchObject({ verified: false });
		if (!unavailable.verified)
			expect(unavailable.diagnostics).toContain("assurance trust anchor resolution failed: registry unavailable");

		const proofFailure: AssuranceVerifierPort = {
			resolveTrustAnchor: () => anchor,
			verifyProof: () => {
				throw new Error("key service unavailable");
			},
		};
		const unverified = verifyAssuranceAttestation(attestation(), expected, proofFailure);
		expect(unverified).toMatchObject({ verified: false });
		if (!unverified.verified)
			expect(unverified.diagnostics).toContain("assurance proof verification failed: key service unavailable");
		const emptyFailure: AssuranceVerifierPort = {
			resolveTrustAnchor: () => anchor,
			verifyProof: () => ({ verified: false, diagnostics: [] }),
		};
		expect(verifyAssuranceAttestation(attestation(), expected, emptyFailure)).toMatchObject({ verified: false });
	});

	it("keeps portable issuers below the Pi enforcement level", () => {
		const value = attestation({
			issuer: { issuer_id: "portable-host", kind: "portable_host", version: "1", digest: digest("f") },
		});
		expect(validateAssuranceAttestation(value)).toContain("assurance profile exceeds the issuer-kind ceiling");
	});

	it("rejects cross-run replay and every digest-binding mismatch", () => {
		const replay = verifyAssuranceAttestation(attestation(), { ...expected, run_id: "another-run" }, verifier());
		expect(replay).toMatchObject({ verified: false });
		for (const field of [
			"request_digest",
			"admission_digest",
			"receipt_digest",
			"execution_history_digest",
			"validation_evidence_digest",
			"subject_digest",
			"approval_event_digest",
			"workflow_digest",
			"validator_digest",
			"executor_digest",
			"policy_digest",
			"policy_snapshot_digest",
			"reference_snapshot_digest",
			"run_binding_digest",
		] as const) {
			const result = verifyAssuranceAttestation(attestation(), { ...expected, [field]: digest("f") }, verifier());
			expect(result.verified, field).toBe(false);
		}
	});

	it("rejects self-digest edits and malformed event chains before consulting trust", () => {
		const edited = { ...attestation(), run_id: "edited-run" };
		expect(validateAssuranceAttestation(edited)).toContain(
			"assurance attestation statement_digest does not match its contents",
		);
		const chained = attestation({ event_sequence: 2 });
		expect(validateAssuranceAttestation(chained)).toContain(
			"subsequent assurance event requires previous_event_digest",
		);
	});
});
