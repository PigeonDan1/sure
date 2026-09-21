import { dirname, resolve } from "node:path";
import { setKeybindings } from "@earendil-works/pi-tui";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { KeybindingsManager } from "../src/core/keybindings.ts";
import { TrustSelectorComponent } from "../src/modes/interactive/components/trust-selector.ts";
import { initTheme } from "../src/modes/interactive/theme/theme.ts";
import { stripAnsi } from "../src/utils/ansi.ts";

// The component keys trust decisions on the resolved, absolute cwd, so the fixtures
// have to be spelled the way the host spells them or the saved decision never matches.
const PROJECT = resolve("/project");
const PROJECT_PARENT = dirname(PROJECT);
const PARENT = resolve("/parent");
const PARENT_PROJECT = resolve("/parent/project");
const NESTED = resolve("/parent/project/nested");

describe("TrustSelectorComponent", () => {
	beforeAll(() => {
		initTheme("dark");
	});

	beforeEach(() => {
		setKeybindings(new KeybindingsManager());
	});

	it("keeps the saved trusted decision marked while browsing", () => {
		const selector = new TrustSelectorComponent({
			cwd: PROJECT,
			savedDecision: { path: PROJECT, decision: true },
			projectTrusted: true,
			onSelect: () => {},
			onCancel: () => {},
		});

		let output = stripAnsi(selector.render(120).join("\n"));
		expect(output).toContain(`Saved decision: trusted (${PROJECT})`);
		expect(output).toContain("Current session: trusted");
		expect(output).toContain("→ ✓ Trust");

		selector.handleInput("\x1b[B");
		output = stripAnsi(selector.render(120).join("\n"));
		expect(output).toContain("✓ Trust");
		expect(output).toContain(`→   Trust parent folder (${PROJECT_PARENT})`);
		expect(output).not.toContain("✓ Do not trust");
	});

	it("selects a trust decision", () => {
		const onSelect = vi.fn();
		const selector = new TrustSelectorComponent({
			cwd: PROJECT,
			savedDecision: null,
			projectTrusted: false,
			onSelect,
			onCancel: () => {},
		});

		selector.handleInput("\n");

		expect(onSelect).toHaveBeenCalledWith({ trusted: true, updates: [{ path: PROJECT, decision: true }] });
	});

	it("labels saved ancestor decisions as inherited", () => {
		const selector = new TrustSelectorComponent({
			cwd: NESTED,
			savedDecision: { path: PARENT, decision: true },
			projectTrusted: true,
			onSelect: () => {},
			onCancel: () => {},
		});

		const output = stripAnsi(selector.render(120).join("\n"));

		expect(output).toContain(`Saved decision: trusted (inherited from ${PARENT})`);
	});

	it("labels a decision saved at the current folder without the inherited prefix", () => {
		const selector = new TrustSelectorComponent({
			cwd: NESTED,
			savedDecision: { path: NESTED, decision: true },
			projectTrusted: true,
			onSelect: () => {},
			onCancel: () => {},
		});

		const output = stripAnsi(selector.render(120).join("\n"));

		expect(output).toContain(`Saved decision: trusted (${NESTED})`);
		expect(output).not.toContain("inherited from");
	});

	it("adds a trust parent option", () => {
		const onSelect = vi.fn();
		const selector = new TrustSelectorComponent({
			cwd: PARENT_PROJECT,
			savedDecision: { path: PARENT, decision: true },
			projectTrusted: true,
			onSelect,
			onCancel: () => {},
		});

		const output = stripAnsi(selector.render(120).join("\n"));
		expect(output).toContain(`Saved decision: trusted (inherited from ${PARENT})`);
		expect(output).toContain(`✓ Trust parent folder (${PARENT})`);

		selector.handleInput("\n");

		expect(onSelect).toHaveBeenCalledWith({
			trusted: true,
			updates: [
				{ path: PARENT, decision: true },
				{ path: PARENT_PROJECT, decision: null },
			],
		});
	});
});
