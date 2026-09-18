import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Resolve @earendil-works/pi-ai to this workspace's sources: CI does not build,
// so packages/ai/dist may not exist, and aliasing also keeps a single instance.
const aiSrcIndex = fileURLToPath(new URL("../ai/src/index.ts", import.meta.url));
const aiSrcDir = fileURLToPath(new URL("../ai/src", import.meta.url));

export default defineConfig({
	test: {
		globals: true,
		environment: "node",
		testTimeout: 30000,
		globalSetup: ["./test/sure-harness-runtime.setup.ts"],
		// Tests run offline by default; opt in with allowNetwork() from test/test-network-env.ts.
		env: { PI_OFFLINE: "1" },
		unstubEnvs: true,
		reporters: process.env.GITHUB_ACTIONS ? ["dot", "github-actions"] : ["dot"],
		silent: "passed-only",
		server: {
			deps: {
				external: [/@silvia-odwyer\/photon-node/],
				// Inline agent-core so its own `import "@earendil-works/pi-ai"` hits the alias too.
				inline: [/@earendil-works\/pi-agent-core/],
			},
		},
	},
	resolve: {
		alias: [
			{ find: /^@earendil-works\/pi-ai$/, replacement: aiSrcIndex },
			{ find: /^@earendil-works\/pi-ai\/(.+)$/, replacement: `${aiSrcDir}/$1.ts` },
		],
	},
});
