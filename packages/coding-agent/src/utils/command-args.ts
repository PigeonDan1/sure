export interface CommandArgsOptions {
	/**
	 * Read `'` as a quote character too, the way bash does. Off by default,
	 * because an apostrophe is an ordinary character in a path: quoting on it
	 * swallows the rest of `C:\Users\O'Brien\vim.exe --wait`. A double quote
	 * works in every shell, so a path with a space can always be spelled "…".
	 */
	singleQuotes?: boolean;
}

export interface CommandArgToken {
	/** The argument with its quotes removed. */
	value: string;
	/** The argument exactly as it was written, quotes included. */
	source: string;
}

/**
 * Parse command arguments respecting quoted strings
 * Returns array of arguments
 */
export function parseCommandArgs(argsString: string, options?: CommandArgsOptions): string[] {
	return tokenizeCommandArgs(argsString, options).map((token) => token.value);
}

/**
 * Parse command arguments, keeping the text each one was written as.
 *
 * A caller that hands some of the arguments on rather than consuming them needs
 * the original spelling: rebuilding them from parsed values drops the quotes
 * the user typed.
 */
export function tokenizeCommandArgs(
	argsString: string,
	{ singleQuotes = false }: CommandArgsOptions = {},
): CommandArgToken[] {
	const tokens: CommandArgToken[] = [];
	let current = "";
	let start = -1;
	let inQuote: string | null = null;

	const flush = (end: number): void => {
		if (current) {
			tokens.push({ value: current, source: argsString.slice(start, end) });
		}
		current = "";
		start = -1;
	};

	for (let i = 0; i < argsString.length; i++) {
		const char = argsString[i];

		if (inQuote) {
			if (char === inQuote) {
				inQuote = null;
			} else {
				current += char;
			}
		} else if (char === '"' || (singleQuotes && char === "'")) {
			if (start < 0) start = i;
			inQuote = char;
		} else if (/\s/.test(char)) {
			flush(i);
		} else {
			if (start < 0) start = i;
			current += char;
		}
	}

	flush(argsString.length);

	return tokens;
}
