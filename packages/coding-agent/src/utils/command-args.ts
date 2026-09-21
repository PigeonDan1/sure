export interface CommandArgsOptions {
	/**
	 * Read `'` as a quote character too, the way bash does. Off by default,
	 * because an apostrophe is an ordinary character in a path: quoting on it
	 * swallows the rest of `C:\Users\O'Brien\vim.exe --wait`. A double quote
	 * works in every shell, so a path with a space can always be spelled "…".
	 */
	singleQuotes?: boolean;
}

/**
 * Parse command arguments respecting quoted strings
 * Returns array of arguments
 */
export function parseCommandArgs(argsString: string, { singleQuotes = false }: CommandArgsOptions = {}): string[] {
	const args: string[] = [];
	let current = "";
	let inQuote: string | null = null;

	for (let i = 0; i < argsString.length; i++) {
		const char = argsString[i];

		if (inQuote) {
			if (char === inQuote) {
				inQuote = null;
			} else {
				current += char;
			}
		} else if (char === '"' || (singleQuotes && char === "'")) {
			inQuote = char;
		} else if (/\s/.test(char)) {
			if (current) {
				args.push(current);
				current = "";
			}
		} else {
			current += char;
		}
	}

	if (current) {
		args.push(current);
	}

	return args;
}
