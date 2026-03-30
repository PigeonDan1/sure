"""
MCP Server for Silero VAD Model.

Implements the Model Context Protocol (MCP) for VAD inference.
Communicates via stdin/stdout using JSON-RPC 2.0.
"""

import sys
import json
import logging
from typing import Any, Dict, List, Optional

from model import VADModel, VADResult

# Configure logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)


class MCPServer:
    """MCP Server for Silero VAD.
    
    Implements JSON-RPC 2.0 over stdin/stdout for MCP protocol.
    """
    
    def __init__(self):
        self.model: Optional[VADModel] = None
        self.model_device = 'cpu'
    
    def handle_initialize(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle initialize request.
        
        Args:
            request: JSON-RPC request
        
        Returns:
            Initialize response
        """
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "silero-vad-mcp",
                    "version": "1.0.0"
                }
            }
        }
    
    def handle_tools_list(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools/list request.
        
        Returns:
            List of available tools
        """
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "tools": [
                    {
                        "name": "vad_predict",
                        "description": "Run Voice Activity Detection on an audio file",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "audio_path": {
                                    "type": "string",
                                    "description": "Path to the audio file"
                                },
                                "sampling_rate": {
                                    "type": "integer",
                                    "description": "Target sampling rate (default: 16000)",
                                    "default": 16000
                                }
                            },
                            "required": ["audio_path"]
                        }
                    },
                    {
                        "name": "healthcheck",
                        "description": "Check if the VAD model is ready",
                        "inputSchema": {
                            "type": "object",
                            "properties": {}
                        }
                    }
                ]
            }
        }
    
    def handle_tools_call(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools/call request.
        
        Args:
            request: JSON-RPC request with tool call
        
        Returns:
            Tool call result
        """
        params = request.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        try:
            if tool_name == "vad_predict":
                return self._handle_vad_predict(request, arguments)
            elif tool_name == "healthcheck":
                return self._handle_healthcheck(request)
            else:
                return self._error_response(
                    request.get("id"),
                    -32601,
                    f"Unknown tool: {tool_name}"
                )
        except Exception as e:
            logger.error(f"Tool call failed: {e}")
            return self._error_response(
                request.get("id"),
                -32000,
                f"Tool execution failed: {e}"
            )
    
    def _handle_vad_predict(self, request: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Handle vad_predict tool call."""
        if self.model is None:
            self.model = VADModel(device=self.model_device)
        
        audio_path = arguments.get("audio_path")
        sampling_rate = arguments.get("sampling_rate", 16000)
        
        if not audio_path:
            return self._error_response(
                request.get("id"),
                -32602,
                "Missing required parameter: audio_path"
            )
        
        result = self.model.predict(audio_path, sampling_rate=sampling_rate)
        
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": result.to_json()
                    }
                ]
            }
        }
    
    def _handle_healthcheck(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle healthcheck tool call."""
        if self.model is None:
            self.model = VADModel(device=self.model_device)
        
        status = self.model.healthcheck()
        
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(status)
                    }
                ]
            }
        }
    
    def _error_response(self, request_id: Any, code: int, message: str) -> Dict[str, Any]:
        """Create an error response."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message
            }
        }
    
    def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle a single JSON-RPC request.
        
        Args:
            request: JSON-RPC request
        
        Returns:
            Response or None for notifications
        """
        method = request.get("method")
        
        if method == "initialize":
            return self.handle_initialize(request)
        elif method == "tools/list":
            return self.handle_tools_list(request)
        elif method == "tools/call":
            return self.handle_tools_call(request)
        elif method == "notifications/initialized":
            # Notification, no response needed
            return None
        else:
            return self._error_response(
                request.get("id"),
                -32601,
                f"Method not found: {method}"
            )
    
    def run(self):
        """Run the MCP server."""
        logger.info("Silero VAD MCP server starting")
        
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            
            try:
                request = json.loads(line)
                response = self.handle_request(request)
                
                if response is not None:
                    print(json.dumps(response), flush=True)
                    
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON: {e}")
                error_response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": f"Parse error: {e}"
                    }
                }
                print(json.dumps(error_response), flush=True)
            except Exception as e:
                logger.error(f"Request handling error: {e}")


if __name__ == "__main__":
    server = MCPServer()
    server.run()
