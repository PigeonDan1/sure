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

		$credentialEnvFile = Join-Path $scriptDir "scripts/credential-env.txt"
		Get-Content -LiteralPath $credentialEnvFile | ForEach-Object {
			$name = $_.Trim()
			if ($name -and -not $name.StartsWith('#')) {
				Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
			}
		}

		Write-Host "Running without API keys..."
	}

	$tsxBin = Join-Path $scriptDir "node_modules/.bin/tsx.cmd"
	if (-not (Test-Path -LiteralPath $tsxBin)) {
		throw "tsx not found at $tsxBin. Run npm install from the repo root first."
	}

	$cliPath = Join-Path $scriptDir "packages/coding-agent/src/cli.ts"
	& $tsxBin $cliPath @forwardArgs
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
