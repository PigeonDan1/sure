import type { Unit } from "./checkpoints.ts";

// SURE model-feed state machine (the xforge→sure_feed port). Discovers
// ModelScope models, matches them to SURE task families, collects metadata,
// converts to the SURE resource (oref) layout, ranks/selects, and emits a
// handoff manifest for /sure_onboard to consume. Linear units are LLM
// self-driven; gate units run validateProduces + a Python semantic gate script.
//
// The Unit contract is the single source of truth in checkpoints.ts; this
// module only populates instances.
//
// Gate-check split principle (no redundancy, no drift):
//   - validateProduces owns STRUCTURE (required fields, enum, additionalProperties
//     /forbiddenFields) for every unit.
//   - The Python gateScript owns SEMANTICS for the two gate units:
//     match_task (every matched candidate has match_source provenance) and
//     rank_and_select (non-empty selection, score ≥ 0, repo present for handoff).
//     No in-process gateCheck is kept — the python scripts are the sole authority,
//     so there is no duplicated === true vs truthy logic.
//
// Source of truth: xforge_sure_bridge/AGENTS.md (+ the renamed sure_feed pkg).

export const MODEL_FEED_UNITS: Unit[] = [
	{
		id: "scan_modelscope",
		label: "Scan ModelScope",
		kind: "linear",
		produces: "scan_result.json",
		schemaRef: "scan_result.schema.json",
		requiredFields: ["candidates"],
		forbiddenFields: ["selected", "handoff_manifest_path"],
	},
	{
		id: "match_task",
		label: "Match to SURE task",
		kind: "gate",
		produces: "match_task_result.json",
		schemaRef: "match_task_result.schema.json",
		requiredFields: ["candidates"],
		gateScript: "check_match_task.py",
	},
	{
		id: "collect_metadata",
		label: "Collect metadata",
		kind: "linear",
		produces: "metadata_result.json",
		schemaRef: "metadata_result.schema.json",
		requiredFields: ["models"],
		forbiddenFields: ["handoff_manifest_path"],
	},
	{
		id: "convert_to_oref",
		label: "Convert to SURE resource layout",
		kind: "linear",
		produces: "oref_result.json",
		schemaRef: "oref_result.schema.json",
		requiredFields: ["converted"],
		forbiddenFields: ["handoff_manifest_path"],
	},
	{
		id: "rank_and_select",
		label: "Rank & select",
		kind: "gate",
		produces: "rank_select_result.json",
		schemaRef: "rank_select_result.schema.json",
		requiredFields: ["selected"],
		gateScript: "check_rank_select.py",
	},
	{
		id: "emit_handoff_manifest",
		label: "Emit handoff manifest",
		kind: "linear",
		produces: "handoff_manifest.json",
		schemaRef: "handoff_manifest.schema.json",
		requiredFields: ["models", "manifest_path"],
	},
];

export const TOTAL_UNITS = MODEL_FEED_UNITS.length;
export const FIRST_UNIT = MODEL_FEED_UNITS[0];
export const LAST_UNIT = MODEL_FEED_UNITS[MODEL_FEED_UNITS.length - 1];

export function findUnit(unitId: string): Unit | undefined {
	return MODEL_FEED_UNITS.find((unit) => unit.id === unitId);
}

export function nextUnit(unitId: string): Unit | undefined {
	const index = MODEL_FEED_UNITS.findIndex((unit) => unit.id === unitId);
	if (index === -1 || index >= MODEL_FEED_UNITS.length - 1) {
		return undefined;
	}
	return MODEL_FEED_UNITS[index + 1];
}
