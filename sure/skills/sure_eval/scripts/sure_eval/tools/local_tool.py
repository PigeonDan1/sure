"""Local tool client for running MCP tools from AUDIO_AGENT."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from sure_eval.core.config import Config
from sure_eval.core.logging import get_logger

logger = get_logger(__name__)


class LocalToolClient:
    """Client for running local MCP tools."""
    
    def __init__(
        self,
        tool_name: str,
        tool_config: dict[str, Any] | None = None,
        config: Config | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.config = config or Config.from_env()
        
        # Get tool config
        if tool_config:
            self.tool_config = tool_config
        else:
            local_tools = self.config.tools.local
            if tool_name not in local_tools:
                raise ValueError(f"Tool {tool_name} not found in local tools config")
            self.tool_config = local_tools[tool_name]
        
        self._process: subprocess.Popen | None = None
        self._initialized = False
        self._tools: list[dict] = []
    
    def start(self) -> None:
        """Start the local tool server."""
        work_dir = self.tool_config.get("work_dir") or self.tool_config.get("path", ".")
        command = self.tool_config.get("command", ["python", "server.py"])
        env_vars = self.tool_config.get("env", {})
        timeout = self.tool_config.get("timeout", 60)
        
        # Build environment
        env = {**dict(os.environ), **env_vars}
        
        logger.info(
            f"Starting local tool: {self.tool_name}",
            work_dir=work_dir,
            command=command,
        )
        
        self._process = subprocess.Popen(
            command,
            cwd=work_dir,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        
        # Initialize
        self._send_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        
        response = self._read_response()
        if "result" not in response:
            raise RuntimeError(f"Failed to initialize tool: {response}")
        
        self._initialized = True
        
        # Get tool list
        self._send_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        
        response = self._read_response()
        self._tools = response.get("result", {}).get("tools", [])
        
        logger.info(
            f"Tool {self.tool_name} started",
            num_tools=len(self._tools),
        )
    
    def stop(self) -> None:
        """Stop the tool server."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
            self._initialized = False
            logger.info(f"Tool {self.tool_name} stopped")
    
    def call(
        self,
        tool_method: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Call a tool method."""
        if not self._initialized:
            raise RuntimeError("Tool not initialized")
        
        logger.debug(
            f"Calling {self.tool_name}.{tool_method}",
            arguments=arguments,
        )
        
        self._send_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": tool_method,
                "arguments": arguments,
            },
        })
        
        response = self._read_response()
        result = response.get("result", {})
        
        if result.get("isError"):
            error = result.get("error", "Unknown error")
            logger.error(f"Tool call failed: {error}")
            raise RuntimeError(f"Tool call failed: {error}")
        
        return result
    
    def transcribe(self, audio_path: str | Path) -> str:
        """Transcribe audio (convenience method for ASR tools)."""
        result = self.call("transcribe", {"audio_path": str(audio_path)})
        
        # Extract text from result
        if "content" in result and len(result["content"]) > 0:
            return result["content"][0].get("text", "")
        return result.get("text", "")
    
    def _send_request(self, request: dict[str, Any]) -> None:
        """Send request to tool."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Tool not running")
        
        request_json = json.dumps(request)
        self._process.stdin.write(request_json + "\n")
        self._process.stdin.flush()
    
    def _read_response(self) -> dict[str, Any]:
        """Read response from tool."""
        if not self._process or not self._process.stdout:
            raise RuntimeError("Tool not running")
        
        line = self._process.stdout.readline()
        if not line:
            raise RuntimeError("Tool returned empty response")
        
        return json.loads(line)
    
    def __enter__(self) -> LocalToolClient:
        self.start()
        return self
    
    def __exit__(self, *args) -> None:
        self.stop()


class ToolRunner:
    """High-level tool runner for evaluation."""
    
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.from_env()
    
    def run_asr(
        self,
        tool_name: str,
        audio_paths: list[str | Path],
        max_workers: int = 1,
    ) -> list[tuple[str, str]]:
        """
        Run ASR tool on multiple audio files.
        
        Returns:
            List of (audio_path, transcription) tuples
        """
        results = []
        
        with LocalToolClient(tool_name, config=self.config) as client:
            for i, audio_path in enumerate(audio_paths):
                try:
                    logger.info(f"Transcribing {i+1}/{len(audio_paths)}: {audio_path}")
                    
                    # Check if the tool has a transcribe method
                    tool_method = "transcribe"
                    
                    result = client.call(tool_method, {"audio_path": str(audio_path)})
                    
                    # Extract text
                    text = ""
                    if "content" in result:
                        content = result["content"]
                        if isinstance(content, list) and len(content) > 0:
                            text = content[0].get("text", "")
                        elif isinstance(content, str):
                            text = content
                    elif "text" in result:
                        text = result["text"]
                    
                    results.append((str(audio_path), text))
                    
                except Exception as e:
                    logger.error(f"Failed to transcribe {audio_path}: {e}")
                    results.append((str(audio_path), ""))
        
        return results
