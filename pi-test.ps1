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
