#!/usr/bin/env node
// Generate the animated README terminal (docs/assets/harness-terminal*.svg).
//
// The SVG replays one canonical SURE session — /sure_init → /sure_feed →
// /sure_onboard → /sure_eval — as a self-contained CSS animation: commands
// type in behind a block cursor, agent output slides in, transient status
// lines resolve into results, and the loop restarts. No scripts, no fonts,
// no network: everything is plain SVG + CSS, so GitHub renders it inside
// <img>/<picture>. `prefers-reduced-motion` freezes the finished session.
//
// Deterministic: same input → byte-identical output. Rerun after changing
// the scenario below. Layout math relies on textLength, so glyph metrics
// are identical on every platform.

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = join(repoRoot, "docs", "assets");

// ---------------------------------------------------------------------------
// Scenario. Kinds: cmd (typed), out (slides in), ok (result), status
// (transient spinner line that is replaced, in place, by the next row).
// ---------------------------------------------------------------------------
const SCENARIO = [
	{ kind: "cmd", cmd: "/sure_init", args: "" },
	{ kind: "ok", text: "provider linked · auth ok · skills discovered · backend checks passed" },
	{ kind: "cmd", cmd: "/sure_feed", args: " https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf" },
	{ kind: "status", text: "researching model card · resolving runtime and I/O contract…", holdS: 1.7 },
	{ kind: "out", text: "task=ASR · runtime=transformers · io=audio→text", replaces: true },
	{ kind: "ok", text: "model_input.yaml · feed_report.json" },
	{ kind: "cmd", cmd: "/sure_onboard", args: " model=Qwen__Qwen3-ASR-0.6B-hf device=auto package=none" },
	{ kind: "status", text: "wrapper synthesis · environment plan · fixture smoke · package gate…", holdS: 1.9 },
	{ kind: "ok", text: "verdict.json · image digest pinned · deployment ready", replaces: true },
	{
		kind: "cmd",
		cmd: "/sure_eval",
		args: " model=Qwen__Qwen3-ASR-0.6B-hf datasets=aishell1 metrics=cer execution=local device=auto",
	},
	{ kind: "out", text: "route asr.zh.cer.aispeech_norm_zh_v1.wenet_cer_v1 · protocol standard_system" },
	{ kind: "status", text: "inference 1/1 · deterministic evaluation pipeline running…", holdS: 2.1 },
	{
		kind: "ok",
		text: "run_report · main_agent_run_report.json · report_persisted=true · execution_path_actual=local_bash",
		replaces: true,
	},
	{ kind: "idle" },
];

// ---------------------------------------------------------------------------
// Geometry, timing, palettes.
// ---------------------------------------------------------------------------
const CH = 7.6; // forced glyph advance via textLength
const FONT = 12.5;
const LINE_H = 22;
const PAD_X = 26;
const TITLE_H = 40;
const PAD_TOP = 14;
const PAD_BOTTOM = 18;
const WIDTH = 860;
const PROMPT = "› ";
const SPINNER = "⠋ ";
const OK_MARK = "✓ ";

const TYPE_S_PER_CHAR = 0.034;
const GAP_AFTER_CMD_S = 0.55;
const GAP_AFTER_LINE_S = 0.5;
const IDLE_TAIL_S = 5.0;
const FADE_S = 0.9;

const THEMES = {
	light: {
		bg: "#ffffff",
		titlebar: "#f4f3f1",
		border: "#e0ded8",
		title: "#8a8a86",
		ink: "#52514e",
		accent: "#1f6f66",
		ok: "#047857",
		muted: "#98968f",
		cursor: "#1f6f66",
		dots: ["#e8615a", "#f5bd4f", "#61c454"],
	},
	dark: {
		bg: "#141716",
		titlebar: "#1d2120",
		border: "#343b39",
		title: "#7d7b73",
		ink: "#b5b3a7",
		accent: "#8abeb7",
		ok: "#7fd1a7",
		muted: "#6d6b64",
		cursor: "#8abeb7",
		dots: ["#e8615a", "#f5bd4f", "#61c454"],
	},
};

// ---------------------------------------------------------------------------
// Build rows and the master timeline (seconds; converted to % of the loop).
// ---------------------------------------------------------------------------
const rows = [];
let row = 0;
let t = 0.6;
for (const step of SCENARIO) {
	if (step.kind === "cmd") {
		const typed = step.cmd + step.args;
		const typeS = typed.length * TYPE_S_PER_CHAR;
		rows.push({ ...step, row, tIn: t, typeS });
		t += typeS + GAP_AFTER_CMD_S;
		row += 1;
	} else if (step.kind === "status") {
		// no row advance: the next step replaces this line in place
		rows.push({ ...step, row, tIn: t, tOut: t + step.holdS });
		t += step.holdS;
	} else if (step.kind === "idle") {
		rows.push({ ...step, row, tIn: t });
		row += 1;
	} else {
		rows.push({ ...step, row, tIn: t });
		t += GAP_AFTER_LINE_S;
		row += 1;
	}
}
const TOTAL_S = t + IDLE_TAIL_S + FADE_S;
const HEIGHT = TITLE_H + PAD_TOP + row * LINE_H + PAD_BOTTOM;
const pct = (s) => ((s / TOTAL_S) * 100).toFixed(3);

const esc = (s) =>
	String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const textEl = (x, y, str, fill, cls = "", weight = "") =>
	`<text class="t ${cls}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" textLength="${(str.length * CH).toFixed(1)}"` +
	`${weight ? ` font-weight="${weight}"` : ""} fill="${fill}">${esc(str)}</text>`;

function render(themeName) {
	const c = THEMES[themeName];
	const body = [];
	const css = [];
	const rowY = (r) => TITLE_H + PAD_TOP + r * LINE_H + LINE_H / 2 + FONT * 0.36;

	css.push(
		`.t{font-family:ui-monospace,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace;font-size:${FONT}px;white-space:pre}`,
		`.line{opacity:0}`,
		`.fade{animation:fade ${TOTAL_S.toFixed(2)}s linear infinite}`,
		`@keyframes fade{0%,${pct(TOTAL_S - FADE_S)}%{opacity:1}${pct(TOTAL_S - FADE_S * 0.35)}%,100%{opacity:0}}`,
	);

	let anim = 0;
	for (const r of rows) {
		const y = rowY(r.row);
		const id = `a${anim++}`;
		if (r.kind === "cmd") {
			const typed = r.cmd + r.args;
			const x0 = PAD_X;
			const promptW = PROMPT.length * CH;
			const typedW = typed.length * CH;
			body.push(`<g class="line ${id}">`);
			body.push(textEl(x0, y, PROMPT, c.accent));
			body.push(textEl(x0 + promptW, y, r.cmd, c.accent, "", "600"));
			if (r.args) {
				body.push(textEl(x0 + promptW + r.cmd.length * CH, y, r.args, c.ink));
			}
			body.push(`</g>`);
			css.push(
				`.${id}{animation:k${id} ${TOTAL_S.toFixed(2)}s linear infinite}`,
				`@keyframes k${id}{0%,${pct(r.tIn)}%{opacity:0}${pct(r.tIn + 0.01)}%,100%{opacity:1}}`,
			);
			// cover slides right in character steps, cursor riding its left edge
			const cid = `c${anim++}`;
			const coverX = x0 + promptW;
			body.push(
				`<g class="cover ${cid}">` +
					`<rect x="${coverX.toFixed(1)}" y="${(y - FONT).toFixed(1)}" width="${(typedW + 4).toFixed(1)}" height="${(FONT * 1.5).toFixed(1)}" fill="${c.bg}"/>` +
					`<rect x="${coverX.toFixed(1)}" y="${(y - FONT + 1).toFixed(1)}" width="${CH.toFixed(1)}" height="${(FONT * 1.3).toFixed(1)}" fill="${c.cursor}"/>` +
					`</g>`,
			);
			css.push(
				`.${cid}{animation:k${cid} ${TOTAL_S.toFixed(2)}s linear infinite;transform:translateX(0)}`,
				`@keyframes k${cid}{0%,${pct(Math.max(0, r.tIn - 0.02))}%{opacity:0;transform:translateX(0)}` +
					`${pct(r.tIn)}%{opacity:1;transform:translateX(0);animation-timing-function:steps(${typed.length})}` +
					`${pct(r.tIn + r.typeS)}%{opacity:1;transform:translateX(${typedW.toFixed(1)}px)}` +
					`${pct(r.tIn + r.typeS + 0.25)}%,100%{opacity:0;transform:translateX(${typedW.toFixed(1)}px)}}`,
			);
		} else if (r.kind === "status") {
			body.push(`<g class="line st ${id}">`);
			body.push(textEl(PAD_X, y, SPINNER + r.text, c.muted));
			body.push(`</g>`);
			css.push(
				`.${id}{animation:k${id} ${TOTAL_S.toFixed(2)}s linear infinite}`,
				`@keyframes k${id}{0%,${pct(r.tIn)}%{opacity:0;transform:translateY(3px)}` +
					`${pct(r.tIn + 0.22)}%{opacity:1;transform:translateY(0)}` +
					`${pct(r.tOut)}%{opacity:1}${pct(r.tOut + 0.12)}%,100%{opacity:0}}`,
			);
		} else if (r.kind === "idle") {
			const bid = `b${anim++}`;
			body.push(
				`<g class="line ${id}">` +
					textEl(PAD_X, y, PROMPT, c.accent) +
					`<g class="${bid}"><rect x="${(PAD_X + PROMPT.length * CH).toFixed(1)}" y="${(y - FONT + 1).toFixed(1)}" width="${CH.toFixed(1)}" height="${(FONT * 1.3).toFixed(1)}" fill="${c.cursor}"/></g>` +
					`</g>`,
			);
			css.push(
				`.${id}{animation:k${id} ${TOTAL_S.toFixed(2)}s linear infinite}`,
				`@keyframes k${id}{0%,${pct(r.tIn)}%{opacity:0}${pct(r.tIn + 0.01)}%,100%{opacity:1}}`,
				`.${bid}{animation:blink 1.06s linear infinite}`,
			);
		} else {
			const fill = r.kind === "ok" ? c.ok : c.ink;
			body.push(`<g class="line ${id}">`);
			if (r.kind === "ok") {
				body.push(textEl(PAD_X, y, OK_MARK, c.ok));
			}
			body.push(textEl(PAD_X + OK_MARK.length * CH, y, r.text, fill));
			body.push(`</g>`);
			css.push(
				`.${id}{animation:k${id} ${TOTAL_S.toFixed(2)}s linear infinite}`,
				`@keyframes k${id}{0%,${pct(r.tIn)}%{opacity:0;transform:translateY(3px)}` +
					`${pct(r.tIn + 0.22)}%,100%{opacity:1;transform:translateY(0)}}`,
			);
		}
	}

	css.push(
		`@keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}`,
		`@media (prefers-reduced-motion:reduce){`,
		`*{animation:none!important}`,
		`.line{opacity:1!important;transform:none!important}`,
		`.st,.cover{opacity:0!important}`,
		`}`,
	);

	const dots = c.dots
		.map((d, i) => `<circle cx="${PAD_X + i * 18}" cy="${TITLE_H / 2}" r="5.2" fill="${d}"/>`)
		.join("");
	const title = "sure-harness";
	return (
		`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${WIDTH} ${HEIGHT}" role="img" aria-label="SURE harness terminal session">` +
		`<title>SURE harness: one /sure_feed → /sure_onboard → /sure_eval session in the interactive terminal</title>` +
		`<style>${css.join("\n")}</style>` +
		`<rect x="1" y="1" width="${WIDTH - 2}" height="${HEIGHT - 2}" rx="12" fill="${c.bg}" stroke="${c.border}" stroke-width="1.5"/>` +
		`<path d="M 1 ${TITLE_H} H ${WIDTH - 1}" stroke="${c.border}" stroke-width="1"/>` +
		dots +
		`<text class="t" x="${WIDTH / 2 - (title.length * CH) / 2}" y="${TITLE_H / 2 + FONT * 0.36}" textLength="${(title.length * CH).toFixed(1)}" fill="${c.title}">${title}</text>` +
		`<g class="fade">${body.join("")}</g>` +
		`</svg>\n`
	);
}

mkdirSync(outDir, { recursive: true });
const outputs = {
	"harness-terminal.svg": render("light"),
	"harness-terminal-dark.svg": render("dark"),
};
for (const [name, content] of Object.entries(outputs)) {
	writeFileSync(join(outDir, name), content);
	console.log(`wrote docs/assets/${name} (${content.length} bytes, loop ${TOTAL_S.toFixed(1)}s, ${row} rows)`);
}
