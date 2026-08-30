function isObject(value) {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sameValue(left, right) {
	return JSON.stringify(left) === JSON.stringify(right);
}

export function legacyValueDifferences(expected, actual, path = "$", options = {}) {
	if (Array.isArray(expected)) {
		if (!Array.isArray(actual)) return [`${path} must remain an array`];
		if (options.orderedArray?.(path)) {
			return expected.flatMap((expectedItem, index) =>
				index < actual.length
					? legacyValueDifferences(expectedItem, actual[index], `${path}[${index}]`, options)
					: [`${path}[${index}] is missing`],
			);
		}
		const unmatched = new Set(actual.map((_, index) => index));
		const differences = [];
		for (const expectedItem of expected) {
			const match = [...unmatched].find(
				(index) => legacyValueDifferences(expectedItem, actual[index], `${path}[]`, options).length === 0,
			);
			if (match === undefined) differences.push(`${path} no longer contains ${JSON.stringify(expectedItem)}`);
			else unmatched.delete(match);
		}
		return differences;
	}
	if (isObject(expected)) {
		if (!isObject(actual)) return [`${path} must remain an object`];
		return Object.entries(expected).flatMap(([key, value]) =>
			Object.hasOwn(actual, key)
				? legacyValueDifferences(value, actual[key], `${path}.${key}`, options)
				: [`${path}.${key} is missing`],
		);
	}
	return Object.is(expected, actual) ? [] : [`${path} changed from ${JSON.stringify(expected)} to ${JSON.stringify(actual)}`];
}

function sameSet(left, right) {
	return left.length === right.length && left.every((value) => right.some((candidate) => sameValue(value, candidate)));
}

export function schemaCompatibilityDifferences(expected, actual, path = "$") {
	if (Array.isArray(expected)) {
		if (!Array.isArray(actual)) return [`${path} must remain an array`];
		if (path.endsWith(".enum")) {
			return expected.every((value) => actual.some((candidate) => sameValue(value, candidate)))
				? []
				: [`${path} removed a legacy value`];
		}
		if (path.endsWith(".required") || path.endsWith(".type")) {
			return sameSet(expected, actual) ? [] : [`${path} changed`];
		}
		return sameValue(expected, actual) ? [] : [`${path} changed`];
	}
	if (!isObject(expected)) {
		return Object.is(expected, actual)
			? []
			: [`${path} changed from ${JSON.stringify(expected)} to ${JSON.stringify(actual)}`];
	}
	if (!isObject(actual)) return [`${path} must remain an object`];

	const differences = [];
	for (const [key, value] of Object.entries(expected)) {
		if (key === "description") continue;
		if (!Object.hasOwn(actual, key)) {
			differences.push(`${path}.${key} is missing`);
			continue;
		}
		if ((key === "properties" || key === "$defs" || key === "definitions") && isObject(value)) {
			if (!isObject(actual[key])) {
				differences.push(`${path}.${key} must remain an object`);
				continue;
			}
			for (const [name, definition] of Object.entries(value)) {
				if (!Object.hasOwn(actual[key], name)) differences.push(`${path}.${key}.${name} is missing`);
				else differences.push(...schemaCompatibilityDifferences(definition, actual[key][name], `${path}.${key}.${name}`));
			}
			continue;
		}
		differences.push(...schemaCompatibilityDifferences(value, actual[key], `${path}.${key}`));
	}

	for (const key of Object.keys(actual)) {
		if (key === "description" || Object.hasOwn(expected, key)) continue;
		differences.push(`${path}.${key} added a new constraint`);
	}
	return differences;
}
