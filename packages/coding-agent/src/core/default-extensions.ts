import type { ExtensionFactory, InlineExtension } from "./extensions/index.ts";
import { sureExtension } from "./sure/index.ts";

const DEFAULT_EXTENSION_FACTORIES: InlineExtension[] = [{ name: "sure", factory: sureExtension }];

export interface DefaultExtensionFactoryOptions {
	includeDefaults?: boolean;
}

function factoryOf(extension: InlineExtension): ExtensionFactory {
	return typeof extension === "function" ? extension : extension.factory;
}

export function withDefaultExtensionFactories(
	factories: InlineExtension[] = [],
	options: DefaultExtensionFactoryOptions = {},
): InlineExtension[] {
	const result = options.includeDefaults === false ? [] : [...DEFAULT_EXTENSION_FACTORIES];
	for (const extension of factories) {
		const factory = factoryOf(extension);
		if (!result.some((existing) => factoryOf(existing) === factory)) {
			result.push(extension);
		}
	}
	return result;
}
