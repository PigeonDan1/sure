"""MCP tool client for SURE-EVAL."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

from sure_eval.core.config import Config
from sure_eval.core.logging import get_logger

logger = get_logger(__name__)


class MCPToolConfig:
    """MCP tool configuration."""
    
    def __init__(
        self,
        name: str,
        command: list[str],
        working_dir: str | Path,
        env: dict[str, str] | None = None,
        timeout: int = 60,
    ) -> None:
        self.name = name
        self.command = command
        self.working_dir = Path(working_dir)
        self.env = env or {}
        self.timeout = timeout


class MCPToolClient:
    """Client for MCP tools."""
    
    def __init__(self, config: MCPToolConfig) -> None:
        self.config = config
        self._process: subprocess.Popen | None = None
        self._initialized = False
        self._tools: list[dict] = []
    
    def start(self) -> None:
        """Start the MCP tool server."""
        logger.info(
            "Starting MCP tool server",
            tool=self.config.name,
            command=self.config.command,
        )
        
        env = {**dict(subprocess.os.environ), **self.config.env}
        
        self._process = subprocess.Popen(
            self.config.command,
            cwd=self.config.working_dir,
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
            raise RuntimeError(f"Failed to initialize: {response}")
        
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
            "MCP tool server started",
            tool=self.config.name,
            num_tools=len(self._tools),
        )
    
    def stop(self) -> None:
        """Stop the MCP tool server."""
        if self._process:
            self._process.terminate()
            self._process.wait()
            self._process = None
            self._initialized = False
            logger.info("MCP tool server stopped", tool=self.config.name)
    
    def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Call a tool."""
        if not self._initialized:
            raise RuntimeError("Tool not initialized")
        
        logger.debug(
            "Calling tool",
            tool=self.config.name,
            method=tool_name,
        )
        
        self._send_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        })
        
        response = self._read_response()
        result = response.get("result", {})
        
        if result.get("isError"):
            error = result.get("error", "Unknown error")
            logger.error("Tool call failed", tool=tool_name, error=error)
            raise RuntimeError(f"Tool call failed: {error}")
        
        return result
    
    def _send_request(self, request: dict[str, Any]) -> None:
        """Send a request to the tool."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Tool not running")
        
        self._process.stdin.write(json.dumps(request) + "\n")
        self._process.stdin.flush()
    
    def _read_response(self) -> dict[str, Any]:
        """Read a response from the tool."""
        if not self._process or not self._process.stdout:
            raise RuntimeError("Tool not running")
        
        line = self._process.stdout.readline()
        return json.loads(line)
    
    def __enter__(self) -> MCPToolClient:
        self.start()
        return self
    
    def __exit__(self, *args) -> None:
        self.stop()


class ToolRegistry:
    """Registry for MCP tools."""
    
    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = config_path or "./config/mcp_tools.yaml"
        self._tools: dict[str, MCPToolConfig] = {}
        self._load_config()
    
    def _load_config(self) -> None:
        """Load tool configurations."""
        path = Path(self.config_path)
        if not path.exists():
            logger.warning("MCP tools config not found", path=str(path))
            return
        
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        
        for name, config in data.get("tools", {}).items():
            self._tools[name] = MCPToolConfig(
                name=name,
                command=config["command"],
                working_dir=config.get("working_dir", "."),
                env=config.get("env", {}),
                timeout=config.get("timeout", 60),
            )
        
        logger.info("Loaded MCP tool configurations", num_tools=len(self._tools))
    
    def get_tool(self, name: str) -> MCPToolConfig | None:
        """Get a tool configuration."""
        return self._tools.get(name)
    
    def list_tools(self) -> list[str]:
        """List available tools."""
        return list(self._tools.keys())
    
    def create_client(self, name: str) -> MCPToolClient:
        """Create a client for a tool."""
        config = self.get_tool(name)
        if not config:
            raise ValueError(f"Unknown tool: {name}")
        return MCPToolClient(config)


class ToolAdapter:
    """Adapter for calling tools in evaluation context."""
    
    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or ToolRegistry()
    
    def _call_with_client(
        self,
        client: MCPToolClient,
        audio_path: str | Path,
        task: str | None = None,
    ) -> dict[str, Any]:
        """Call the task-specific MCP method using an existing client."""
        audio_path = str(audio_path)
        
        # Determine the right tool method based on task
        if task == "ASR":
            result = client.call("transcribe", {"audio_path": audio_path})
        elif task == "SER":
            result = client.call("recognize_emotion", {"audio_path": audio_path})
        elif task == "SD":
            result = client.call("diarize", {"audio_path": audio_path})
        else:
            # Try generic invoke method
            result = client.call("invoke", {
                "audio_path": audio_path,
                "task": task,
            })
        
        return result

    def evaluate_audio(
        self,
        tool_name: str,
        audio_path: str | Path,
        task: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate an audio file using a tool."""
        with self.registry.create_client(tool_name) as client:
            return self._call_with_client(client, audio_path, task)
    
    def batch_evaluate(
        self,
        tool_name: str,
        audio_paths: list[str | Path],
        task: str | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate multiple audio files."""
        results = []
        with self.registry.create_client(tool_name) as client:
            for path in audio_paths:
                try:
                    result = self._call_with_client(client, path, task)
                    results.append({"path": str(path), "success": True, "result": result})
                except Exception as e:
                    results.append({"path": str(path), "success": False, "error": str(e)})
        
        return results
