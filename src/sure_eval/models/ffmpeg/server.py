"""
FFmpeg MCP Server for SURE-EVAL.

Responsibilities:
- Implement MCP protocol (initialize, tools/list, tools/call)
- Parse JSON-RPC requests from stdin
- Delegate to FFmpegWrapper for processing
- Format responses to stdout

Input: stdin (JSON-RPC)
Output: stdout (JSON-RPC)
Logs: stderr
"""

import sys
import json
import logging
from typing import Dict, Any, Optional

# Configure logging to stderr
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ffmpeg_mcp_server')


class MCPServer:
    """MCP Server for FFmpeg audio processing."""
    
    def __init__(self):
        self._initialized = False
        self._wrapper = None
        
    def _get_wrapper(self):
        """Lazy load wrapper."""
        if self._wrapper is None:
            # Delay import to avoid loading at module level
            from .model import FFmpegWrapper
            self._wrapper = FFmpegWrapper()
        return self._wrapper
    
    def handle_initialize(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle initialize request."""
        self._initialized = True
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "ffmpeg-mcp-server",
                    "version": "1.0.0"
                }
            }
        }
    
    def handle_tools_list(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools/list request."""
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "tools": [
                    {
                        "name": "process_audio",
                        "description": "Process audio file using FFmpeg (clip, resample, convert format)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "input_path": {
                                    "type": "string",
                                    "description": "Path to input audio file"
                                },
                                "output_path": {
                                    "type": "string",
                                    "description": "Path for output audio file"
                                },
                                "start_time": {
                                    "type": "number",
                                    "description": "Start time in seconds (optional)"
                                },
                                "duration": {
                                    "type": "number",
                                    "description": "Duration in seconds (optional)"
                                },
                                "sample_rate": {
                                    "type": "integer",
                                    "description": "Target sample rate in Hz (optional)"
                                },
                                "channels": {
                                    "type": "integer",
                                    "description": "Target number of channels (optional)"
                                }
                            },
                            "required": ["input_path", "output_path"]
                        }
                    },
                    {
                        "name": "healthcheck",
                        "description": "Check if FFmpeg tools are available",
                        "inputSchema": {
                            "type": "object",
                            "properties": {}
                        }
                    }
                ]
            }
        }
    
    def handle_tools_call(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools/call request."""
        params = request.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        request_id = request.get("id")
        
        try:
            if tool_name == "process_audio":
                return self._handle_process_audio(arguments, request_id)
            elif tool_name == "healthcheck":
                return self._handle_healthcheck(request_id)
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Unknown tool: {tool_name}"
                    }
                }
        except Exception as e:
            logger.exception("Tool execution failed")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32000,
                    "message": f"Tool execution failed: {e}"
                }
            }
    
    def _handle_process_audio(
        self,
        arguments: Dict[str, Any],
        request_id: Any
    ) -> Dict[str, Any]:
        """Handle process_audio tool call."""
        from .model import InferenceError
        
        wrapper = self._get_wrapper()
        
        input_path = arguments.get("input_path")
        output_path = arguments.get("output_path")
        
        if not input_path or not output_path:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32602,
                    "message": "Missing required arguments: input_path, output_path"
                }
            }
        
        try:
            result = wrapper.process_audio(
                input_path=input_path,
                output_path=output_path,
                start_time=arguments.get("start_time"),
                duration=arguments.get("duration"),
                sample_rate=arguments.get("sample_rate"),
                channels=arguments.get("channels")
            )
            
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, indent=2)
                        }
                    ]
                }
            }
        except InferenceError as e:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32000,
                    "message": f"Audio processing failed: {e}"
                }
            }
    
    def _handle_healthcheck(self, request_id: Any) -> Dict[str, Any]:
        """Handle healthcheck tool call."""
        wrapper = self._get_wrapper()
        health = wrapper.healthcheck()
        
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(health, indent=2)
                    }
                ]
            }
        }
    
    def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Route request to appropriate handler."""
        method = request.get("method")
        
        if method == "initialize":
            return self.handle_initialize(request)
        elif method == "tools/list":
            return self.handle_tools_list(request)
        elif method == "tools/call":
            return self.handle_tools_call(request)
        else:
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }
    
    def run(self):
        """Run the MCP server (read from stdin, write to stdout)."""
        logger.info("FFmpeg MCP Server starting")
        
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            
            try:
                request = json.loads(line)
                response = self.handle_request(request)
                if response:
                    print(json.dumps(response), flush=True)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON: {e}")
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": f"Parse error: {e}"
                    }
                }
                print(json.dumps(response), flush=True)


if __name__ == "__main__":
    server = MCPServer()
    server.run()
