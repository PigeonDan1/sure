import { createJiti } from "jiti/static";
import { isBunBinary } from "../../config.ts";
import { resolveSurePackagePath } from "./manifest.ts";
import { getSureHookAliases, getSureHookVirtualModules } from "./module-loader.ts";
import type { SureHookContext, SureHookDeclaration, SureHookPoint, SureHookResult, SureSkillPackage } from "./types.ts";

type HookFunction = (context: SureHookContext) => SureHookResult | Promise<SureHookResult | undefined> | undefined;

export interface SureGateResult {
	ok: boolean;
	message?: string;
	repair?: string;
	diagnostics?: unknown;
	state_patch?: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null;
}

function normalizeHookResult(value: unknown): SureHookResult {
	if (!isRecord(value)) {
		return {};
	}
	return {
		ok: typeof value.ok === "boolean" ? value.ok : undefined,
		message: typeof value.message === "string" ? value.message : undefined,
		repair: typeof value.repair === "string" ? value.repair : undefined,
		patch: value.patch,
		artifacts: value.artifacts,
		diagnostics: value.diagnostics,
		state_patch: value.state_patch,
	};
}

function toGateFailure(result: SureHookResult, fallbackMessage: string): SureGateResult | undefined {
	if (result.ok !== false && !result.repair) {
		return undefined;
	}
	return {
		ok: false,
		message: result.message ?? fallbackMessage,
		repair: result.repair ?? result.message ?? fallbackMessage,
		diagnostics: result.diagnostics,
		state_patch: result.state_patch,
	};
}

export class SureHookRunner {
	private skillPackage: SureSkillPackage;
	private jiti = createJiti(import.meta.url, {
		moduleCache: false,
		...(isBunBinary
			? { virtualModules: getSureHookVirtualModules(), tryNative: false }
			: { alias: getSureHookAliases() }),
	});

	constructor(skillPackage: SureSkillPackage) {
		this.skillPackage = skillPackage;
	}

	async run(point: SureHookPoint, context: Omit<SureHookContext, "point">): Promise<SureGateResult> {
		const declarations = this.skillPackage.manifest.hooks?.[point] ?? [];
		let statePatch: unknown;
		for (const declaration of declarations) {
			const result = await this.runHook(point, declaration, context);
			if (result.state_patch !== undefined) {
				statePatch = result.state_patch;
			}
			const failure = toGateFailure(result, `Sure ${point} hook rejected the action.`);
			if (failure) {
				return { ...failure, state_patch: failure.state_patch ?? statePatch };
			}
		}
		return { ok: true, state_patch: statePatch };
	}

	private async runHook(
		point: SureHookPoint,
		declaration: SureHookDeclaration,
		context: Omit<SureHookContext, "point">,
	): Promise<SureHookResult> {
		const hookPath = resolveSurePackagePath(this.skillPackage.packageDir, declaration.module);
		if (!hookPath) {
			return {
				ok: false,
				message: `Hook module path escapes skill package: ${declaration.module}`,
				repair: "Fix sure.skill.json so hook module paths stay inside the skill package.",
			};
		}

		try {
			const loaded = await this.jiti.import(hookPath);
			const moduleValue = loaded as Record<string, unknown>;
			const handlerName = declaration.handler ?? point;
			const handler =
				(typeof moduleValue[handlerName] === "function" ? moduleValue[handlerName] : undefined) ??
				(typeof moduleValue.default === "function" ? moduleValue.default : undefined);
			if (!handler) {
				return {
					ok: false,
					message: `Hook handler not found: ${handlerName}`,
					repair: `Export a function named "${handlerName}" from ${declaration.module}, or provide a default export.`,
				};
			}

			const value = await (handler as HookFunction)({
				...context,
				point,
			});
			return normalizeHookResult(value);
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			return {
				ok: false,
				message: `Sure ${point} hook failed: ${message}`,
				repair: `Fix the hook failure in ${declaration.module} and retry the action.`,
			};
		}
	}
}
