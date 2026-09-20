$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$noEnv = $false
$forwardArgs = New-Object System.Collections.Generic.List[string]
$authBackup = $null
$authFileToRestore = $null

foreach ($arg in $args) {
	if ($arg -eq "--no-env") {
		$noEnv = $true
	} else {
		$forwardArgs.Add($arg)
	}
}

try {
	if ($noEnv) {
		if ($env:PI_CODING_AGENT_DIR) {
			$agentDir = $env:PI_CODING_AGENT_DIR
		} else {
			$agentDir = Join-Path $HOME ".pi/agent"
		}
		$authFile = Join-Path $agentDir "auth.json"
		if (Test-Path -LiteralPath $authFile) {
			$authBackup = "$authFile.bak.$PID"
			$authFileToRestore = $authFile
			Move-Item -LiteralPath $authFile -Destination $authBackup
			Write-Host "Moved auth.json to backup"
		}

		Write-Host "Running without stored credentials..."
	}

	if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
		throw "node was not found on PATH. Install Node 22.19 or newer (see .nvmrc)."
	}

	& node -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit(major > 22 || (major === 22 && minor >= 19) ? 0 : 1);"
	if ($LASTEXITCODE -ne 0) {
		throw "Node $(node --version) is too old. This repository needs Node 22.19 or newer (see .nvmrc)."
	}

	if ((Get-Location).Path -ne $scriptDir) {
		Write-Warning "Running from $((Get-Location).Path), not the repository root $scriptDir."
		Write-Warning "The agent's working directory is the one you started from."
	}

	$agentCoreDir = Join-Path $scriptDir "node_modules/@earendil-works/pi-agent-core"
	if (-not (Test-Path -LiteralPath $agentCoreDir)) {
		throw "Missing node_modules/@earendil-works/pi-agent-core. Run npm install --ignore-scripts, then npm run sure:doctor, from the repository root."
	}

	$sureCoreDir = Join-Path $scriptDir "packages/coding-agent/src/core/sure"
	# The probe swallows its own error: redirecting a native command's stderr under
	# ErrorActionPreference Stop makes Windows PowerShell throw NativeCommandError
	# before the message below is ever reached.
	& node -e "const base = process.argv[1]; try { for (const p of ['typebox','typebox/compile','typebox/value']) require.resolve(p, { paths: [base] }); } catch { process.exit(1); }" $sureCoreDir
	if ($LASTEXITCODE -ne 0) {
		throw "Missing SURE runtime dependency: typebox. Run npm install --ignore-scripts, then npm run sure:doctor, from the repository root."
	}

	$resolverPath = Join-Path $scriptDir "packages/coding-agent/test/source-resolver.ts"
	if (-not (Test-Path -LiteralPath $resolverPath)) {
		throw "Source resolver not found at $resolverPath."
	}

	# Node resolves --import as a URL, so a Windows path needs a file:// URL.
	$resolverUrl = ([System.Uri]$resolverPath).AbsoluteUri
	$cliPath = Join-Path $scriptDir "packages/coding-agent/src/cli.ts"
	& node --import $resolverUrl $cliPath @forwardArgs
	$exitCode = $LASTEXITCODE
	if ($exitCode -ne 0) {
		exit $exitCode
	}
} finally {
	if ($authBackup -and $authFileToRestore -and (Test-Path -LiteralPath $authBackup)) {
		Move-Item -LiteralPath $authBackup -Destination $authFileToRestore -Force
		Write-Host "Restored auth.json"
	}
}
