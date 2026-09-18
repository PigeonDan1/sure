import { join } from "node:path";
import chalk from "chalk";
import { selectConfig } from "./cli/config-selector.ts";
import { createProjectTrustContext } from "./cli/project-trust.ts";
import { APP_NAME, CONFIG_DIR_NAME, getAgentDir } from "./config.ts";
import type { InlineExtension } from "./core/extensions/types.ts";
import { ModelRuntime } from "./core/model-runtime.ts";
import { DefaultPackageManager } from "./core/package-manager.ts";
import { type AppMode, resolveProjectTrusted } from "./core/project-trust.ts";
import { DefaultResourceLoader } from "./core/resource-loader.ts";
import { SettingsManager } from "./core/settings-manager.ts";
import { hasTrustRequiringProjectResources, ProjectTrustStore } from "./core/trust-manager.ts";

export type PackageCommand = "install" | "remove" | "update" | "list";

type UpdateTarget = { type: "extensions"; source?: string } | { type: "models" };

interface PackageCommandOptions {
	command: PackageCommand;
	source?: string;
	updateTarget?: UpdateTarget;
	local: boolean;
	projectTrustOverride?: boolean;
	help: boolean;
	invalidOption?: string;
	invalidArgument?: string;
	missingOptionValue?: string;
	conflictingOptions?: string;
}

function reportSettingsErrors(settingsManager: SettingsManager, context: string): void {
	const errors = settingsManager.drainErrors();
	for (const { scope, error } of errors) {
		console.error(chalk.yellow(`Warning (${context}, ${scope} settings): ${error.message}`));
		if (error.stack) {
			console.error(chalk.dim(error.stack));
		}
	}
}

function getPackageCommandUsage(command: PackageCommand): string {
	switch (command) {
		case "install":
			return `${APP_NAME} install <source> [-l] [--approve|--no-approve]`;
		case "remove":
			return `${APP_NAME} remove <source> [-l] [--approve|--no-approve]`;
		case "update":
			return `${APP_NAME} update [source] [--extensions|--models] [--extension <source>] [--approve|--no-approve]`;
		case "list":
			return `${APP_NAME} list [--approve|--no-approve]`;
	}
}

const CONFIG_COMMAND_USAGE = `${APP_NAME} config [-l] [--approve|--no-approve]`;

function printConfigCommandHelp(): void {
	console.log(`${chalk.bold("Usage:")}
  ${CONFIG_COMMAND_USAGE}

Open the resource configuration TUI to enable or disable package resources.
Without -l, starts in global settings (~/${CONFIG_DIR_NAME}/agent/settings.json).
Press Tab in the TUI to switch between global and project-local modes.

Options:
  -l, --local       Edit project overrides (${CONFIG_DIR_NAME}/settings.json)
  -a, --approve     Trust project-local files for this command with -l
  -na, --no-approve Ignore project-local files for this command with -l
`);
}

function printPackageCommandHelp(command: PackageCommand): void {
	switch (command) {
		case "install":
			console.log(`${chalk.bold("Usage:")}
  ${getPackageCommandUsage("install")}

Install a package and add it to settings.

Options:
  -l, --local       Install project-locally (${CONFIG_DIR_NAME}/settings.json)
  -a, --approve     Trust project-local files for this command
  -na, --no-approve Ignore project-local files for this command

Examples:
  ${APP_NAME} install npm:@foo/bar
  ${APP_NAME} install git:github.com/user/repo
  ${APP_NAME} install git:git@github.com:user/repo
  ${APP_NAME} install https://github.com/user/repo
  ${APP_NAME} install ssh://git@github.com/user/repo
  ${APP_NAME} install ./local/path
`);
			return;

		case "remove":
			console.log(`${chalk.bold("Usage:")}
  ${getPackageCommandUsage("remove")}

Remove a package and its source from settings.
Alias: ${APP_NAME} uninstall <source> [-l]

Options:
  -l, --local       Remove from project settings (${CONFIG_DIR_NAME}/settings.json)
  -a, --approve     Trust project-local files for this command
  -na, --no-approve Ignore project-local files for this command

Examples:
  ${APP_NAME} remove npm:@foo/bar
  ${APP_NAME} uninstall npm:@foo/bar
`);
			return;

		case "update":
			console.log(`${chalk.bold("Usage:")}
  ${getPackageCommandUsage("update")}

Update installed packages or model catalogs.

Options:
  --extensions            Update installed packages (default)
  --models                Refresh model catalogs only
  --extension <source>    Update one package only
  -a, --approve           Trust project-local files for this command
  -na, --no-approve       Ignore project-local files for this command

Short forms:
  ${APP_NAME} update                Update all installed packages
  ${APP_NAME} update --models       Refresh model catalogs only
  ${APP_NAME} update <source>       Update one package
`);
			return;

		case "list":
			console.log(`${chalk.bold("Usage:")}
  ${getPackageCommandUsage("list")}

List installed packages from user and project settings.

Options:
  -a, --approve      Trust project-local files for this command
  -na, --no-approve  Ignore project-local files for this command
`);
			return;
	}
}

function parsePackageCommand(args: string[]): PackageCommandOptions | undefined {
	const [rawCommand, ...rest] = args;
	let command: PackageCommand | undefined;
	if (rawCommand === "uninstall") {
		command = "remove";
	} else if (rawCommand === "install" || rawCommand === "remove" || rawCommand === "update" || rawCommand === "list") {
		command = rawCommand;
	}
	if (!command) {
		return undefined;
	}

	let local = false;
	let projectTrustOverride: boolean | undefined;
	let help = false;
	let invalidOption: string | undefined;
	let invalidArgument: string | undefined;
	let missingOptionValue: string | undefined;
	let conflictingOptions: string | undefined;
	let source: string | undefined;
	let extensionsFlag = false;
	let modelsFlag = false;
	let extensionFlagSource: string | undefined;

	for (let index = 0; index < rest.length; index++) {
		const arg = rest[index];
		if (arg === "-h" || arg === "--help") {
			help = true;
			continue;
		}

		if (arg === "-l" || arg === "--local") {
			if (command === "install" || command === "remove") {
				local = true;
			} else {
				invalidOption = invalidOption ?? arg;
			}
			continue;
		}

		if (arg === "--extensions") {
			if (command === "update") {
				extensionsFlag = true;
			} else {
				invalidOption = invalidOption ?? arg;
			}
			continue;
		}

		if (arg === "--models") {
			if (command === "update") {
				modelsFlag = true;
			} else {
				invalidOption = invalidOption ?? arg;
			}
			continue;
		}

		if (arg === "--approve" || arg === "-a") {
			projectTrustOverride = true;
			continue;
		}

		if (arg === "--no-approve" || arg === "-na") {
			projectTrustOverride = false;
			continue;
		}

		if (arg === "--extension") {
			if (command !== "update") {
				invalidOption = invalidOption ?? arg;
				continue;
			}

			const value = rest[index + 1];
			if (!value || value.startsWith("-")) {
				missingOptionValue = missingOptionValue ?? arg;
			} else if (extensionFlagSource) {
				conflictingOptions = conflictingOptions ?? "--extension can only be provided once";
				index++;
			} else {
				extensionFlagSource = value;
				index++;
			}
			continue;
		}

		if (arg.startsWith("-")) {
			invalidOption = invalidOption ?? arg;
			continue;
		}

		if (!source) {
			source = arg;
		} else {
			invalidArgument = invalidArgument ?? arg;
		}
	}

	let updateTarget: UpdateTarget | undefined;
	if (command === "update") {
		if (modelsFlag) {
			if (extensionsFlag || extensionFlagSource) {
				conflictingOptions = conflictingOptions ?? "--models cannot be combined with --extensions or --extension";
			}
			if (source) {
				conflictingOptions = conflictingOptions ?? "--models cannot be combined with a positional source";
			}
			updateTarget = { type: "models" };
		} else if (extensionFlagSource) {
			if (extensionsFlag) {
				conflictingOptions = conflictingOptions ?? "--extension cannot be combined with --extensions";
			}
			if (source) {
				conflictingOptions = conflictingOptions ?? "--extension cannot be combined with a positional source";
			}
			updateTarget = { type: "extensions", source: extensionFlagSource };
		} else if (source) {
			if (extensionsFlag) {
				conflictingOptions = conflictingOptions ?? "positional update targets cannot be combined with --extensions";
			}
			updateTarget = { type: "extensions", source };
		} else {
			updateTarget = { type: "extensions" };
		}
	}

	return {
		command,
		source,
		updateTarget,
		local,
		projectTrustOverride,
		help,
		invalidOption,
		invalidArgument,
		missingOptionValue,
		conflictingOptions,
	};
}

async function refreshModelCatalogs(agentDir: string): Promise<void> {
	const controller = new AbortController();
	const timeout = setTimeout(() => controller.abort(), 15_000);
	try {
		const modelRuntime = await ModelRuntime.create({
			authPath: join(agentDir, "auth.json"),
			modelsPath: join(agentDir, "models.json"),
			allowModelNetwork: false,
			signal: controller.signal,
		});
		const result = await modelRuntime.refresh({
			allowNetwork: true,
			force: true,
			signal: controller.signal,
		});
		if (result.aborted) {
			throw new Error("Model catalog refresh timed out.");
		}
		if (result.errors.size > 0) {
			const details = Array.from(result.errors, ([provider, error]) => `${provider}: ${error.message}`).join("; ");
			throw new Error(`Could not refresh model catalogs: ${details}`);
		}
	} finally {
		clearTimeout(timeout);
	}
	console.log(chalk.green("Model catalogs refreshed"));
}

export interface PackageCommandRuntimeOptions {
	extensionFactories?: InlineExtension[];
}

interface CommandSettingsResult {
	settingsManager: SettingsManager;
	projectTrustWarnings: string[];
}

function getCommandAppMode(): AppMode {
	return process.stdin.isTTY && process.stdout.isTTY ? "interactive" : "print";
}

function reportProjectTrustWarnings(warnings: readonly string[]): void {
	for (const warning of warnings) {
		console.error(chalk.yellow(`Warning: ${warning}`));
	}
}

async function createCommandSettingsManager(options: {
	cwd: string;
	agentDir: string;
	projectTrustOverride?: boolean;
	useSavedProjectTrustOnly?: boolean;
	extensionFactories?: InlineExtension[];
}): Promise<CommandSettingsResult> {
	const settingsManager = SettingsManager.create(options.cwd, options.agentDir, { projectTrusted: false });
	const projectTrustWarnings: string[] = [];
	const trustStore = new ProjectTrustStore(options.agentDir);
	if (options.useSavedProjectTrustOnly) {
		const savedProjectTrusted = trustStore.get(options.cwd) === true;
		settingsManager.setProjectTrusted(options.projectTrustOverride ?? savedProjectTrusted);
		return { settingsManager, projectTrustWarnings };
	}

	const appMode = getCommandAppMode();
	const extensionsResult =
		options.projectTrustOverride === undefined && hasTrustRequiringProjectResources(options.cwd)
			? await new DefaultResourceLoader({
					cwd: options.cwd,
					agentDir: options.agentDir,
					settingsManager,
					extensionFactories: options.extensionFactories,
				}).loadProjectTrustExtensions()
			: undefined;
	for (const error of extensionsResult?.errors ?? []) {
		projectTrustWarnings.push(`Failed to load extension "${error.path}": ${error.error}`);
	}

	const projectTrusted = await resolveProjectTrusted({
		cwd: options.cwd,
		trustStore,
		trustOverride: options.projectTrustOverride,
		defaultProjectTrust: settingsManager.getDefaultProjectTrust(),
		extensionsResult,
		projectTrustContext: createProjectTrustContext({
			cwd: options.cwd,
			mode: appMode,
			settingsManager,
			hasUI: appMode === "interactive",
		}),
		onExtensionError: (message) => projectTrustWarnings.push(message),
	});
	settingsManager.setProjectTrusted(projectTrusted);
	return { settingsManager, projectTrustWarnings };
}

export async function handleConfigCommand(
	args: string[],
	runtimeOptions: PackageCommandRuntimeOptions = {},
): Promise<boolean> {
	const [command, ...rest] = args;
	if (command !== "config") {
		return false;
	}

	if (rest.includes("-h") || rest.includes("--help")) {
		printConfigCommandHelp();
		return true;
	}

	let local = false;
	let projectTrustOverride: boolean | undefined;
	for (const arg of rest) {
		if (arg === "-l" || arg === "--local") {
			local = true;
		} else if (arg === "-a" || arg === "--approve") {
			projectTrustOverride = true;
		} else if (arg === "-na" || arg === "--no-approve") {
			projectTrustOverride = false;
		} else if (arg.startsWith("-")) {
			console.error(chalk.red(`Unknown option ${arg} for "config".`));
			console.error(chalk.dim(`Use "${APP_NAME} --help" or "${CONFIG_COMMAND_USAGE}".`));
			process.exitCode = 1;
			return true;
		} else {
			console.error(chalk.red(`Unexpected argument ${arg}.`));
			console.error(chalk.dim(`Usage: ${CONFIG_COMMAND_USAGE}`));
			process.exitCode = 1;
			return true;
		}
	}

	const cwd = process.cwd();
	const agentDir = getAgentDir();
	const { settingsManager, projectTrustWarnings } = await createCommandSettingsManager({
		cwd,
		agentDir,
		projectTrustOverride,
		extensionFactories: runtimeOptions.extensionFactories,
	});
	reportProjectTrustWarnings(projectTrustWarnings);
	if (local && !settingsManager.isProjectTrusted()) {
		console.error(chalk.red("Project is not trusted. Use --approve to modify local resource config."));
		process.exitCode = 1;
		return true;
	}
	reportSettingsErrors(settingsManager, "config command");
	const globalSettingsManager = SettingsManager.create(cwd, agentDir, { projectTrusted: false });
	const globalResolvedPaths = await new DefaultPackageManager({
		cwd,
		agentDir,
		settingsManager: globalSettingsManager,
	}).resolve();
	const projectResolvedPaths = settingsManager.isProjectTrusted()
		? await new DefaultPackageManager({ cwd, agentDir, settingsManager }).resolve()
		: globalResolvedPaths;

	await selectConfig({
		resolvedPaths: { global: globalResolvedPaths, project: projectResolvedPaths },
		settingsManager,
		cwd,
		agentDir,
		writeScope: local ? "project" : "global",
		projectModeAvailable: settingsManager.isProjectTrusted(),
	});

	process.exit(0);
}

export async function handlePackageCommand(
	args: string[],
	runtimeOptions: PackageCommandRuntimeOptions = {},
): Promise<boolean> {
	const options = parsePackageCommand(args);
	if (!options) {
		return false;
	}

	if (options.help) {
		printPackageCommandHelp(options.command);
		return true;
	}

	if (options.invalidOption) {
		console.error(chalk.red(`Unknown option ${options.invalidOption} for "${options.command}".`));
		console.error(chalk.dim(`Use "${APP_NAME} --help" or "${getPackageCommandUsage(options.command)}".`));
		process.exitCode = 1;
		return true;
	}

	if (options.missingOptionValue) {
		console.error(chalk.red(`Missing value for ${options.missingOptionValue}.`));
		console.error(chalk.dim(`Usage: ${getPackageCommandUsage(options.command)}`));
		process.exitCode = 1;
		return true;
	}

	if (options.invalidArgument) {
		console.error(chalk.red(`Unexpected argument ${options.invalidArgument}.`));
		console.error(chalk.dim(`Usage: ${getPackageCommandUsage(options.command)}`));
		process.exitCode = 1;
		return true;
	}

	if (options.conflictingOptions) {
		console.error(chalk.red(options.conflictingOptions));
		console.error(chalk.dim(`Usage: ${getPackageCommandUsage(options.command)}`));
		process.exitCode = 1;
		return true;
	}

	const source = options.source;
	if ((options.command === "install" || options.command === "remove") && !source) {
		console.error(chalk.red(`Missing ${options.command} source.`));
		console.error(chalk.dim(`Usage: ${getPackageCommandUsage(options.command)}`));
		process.exitCode = 1;
		return true;
	}

	if (options.command === "update" && options.updateTarget?.type === "models") {
		try {
			await refreshModelCatalogs(getAgentDir());
		} catch (error: unknown) {
			const message = error instanceof Error ? error.message : "Unknown model catalog refresh error";
			console.error(chalk.red(`Error: ${message}`));
			process.exitCode = 1;
		}
		return true;
	}

	const cwd = process.cwd();
	const agentDir = getAgentDir();
	const writesProjectPackageConfig = (options.command === "install" || options.command === "remove") && options.local;
	const { settingsManager, projectTrustWarnings } = await createCommandSettingsManager({
		cwd,
		agentDir,
		projectTrustOverride: options.projectTrustOverride,
		useSavedProjectTrustOnly: options.command === "update",
		extensionFactories: runtimeOptions.extensionFactories,
	});
	reportProjectTrustWarnings(projectTrustWarnings);
	if (!settingsManager.isProjectTrusted() && writesProjectPackageConfig) {
		console.error(chalk.red("Project is not trusted. Use --approve to modify local package config."));
		process.exitCode = 1;
		return true;
	}
	reportSettingsErrors(settingsManager, "package command");

	const packageManager = new DefaultPackageManager({ cwd, agentDir, settingsManager });

	packageManager.setProgressCallback((event) => {
		if (event.type === "start") {
			process.stdout.write(chalk.dim(`${event.message}\n`));
		}
	});

	try {
		switch (options.command) {
			case "install":
				await packageManager.installAndPersist(source!, { local: options.local });
				console.log(chalk.green(`Installed ${source}`));
				return true;

			case "remove": {
				const removed = await packageManager.removeAndPersist(source!, { local: options.local });
				if (!removed) {
					console.error(chalk.red(`No matching package found for ${source}`));
					process.exitCode = 1;
					return true;
				}
				console.log(chalk.green(`Removed ${source}`));
				return true;
			}

			case "list": {
				const configuredPackages = packageManager.listConfiguredPackages();
				const userPackages = configuredPackages.filter((pkg) => pkg.scope === "user");
				const projectPackages = configuredPackages.filter((pkg) => pkg.scope === "project");

				if (configuredPackages.length === 0) {
					console.log(chalk.dim("No packages installed."));
					return true;
				}

				const formatPackage = (pkg: (typeof configuredPackages)[number]) => {
					const display = pkg.filtered ? `${pkg.source} (filtered)` : pkg.source;
					console.log(`  ${display}`);
					if (pkg.installedPath) {
						console.log(chalk.dim(`    ${pkg.installedPath}`));
					}
				};

				if (userPackages.length > 0) {
					console.log(chalk.bold("User packages:"));
					for (const pkg of userPackages) {
						formatPackage(pkg);
					}
				}

				if (projectPackages.length > 0) {
					if (userPackages.length > 0) console.log();
					console.log(chalk.bold("Project packages:"));
					for (const pkg of projectPackages) {
						formatPackage(pkg);
					}
				}

				return true;
			}

			case "update": {
				const target = options.updateTarget;
				const updateSource = target?.type === "extensions" ? target.source : undefined;
				await packageManager.update(updateSource);
				if (updateSource) {
					console.log(chalk.green(`Updated ${updateSource}`));
				} else {
					console.log(chalk.green("Updated packages"));
				}
				return true;
			}
		}
	} catch (error: unknown) {
		const message = error instanceof Error ? error.message : "Unknown package command error";
		console.error(chalk.red(`Error: ${message}`));
		process.exitCode = 1;
		return true;
	}
}
