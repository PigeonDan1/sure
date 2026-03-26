#!/usr/bin/env python3
"""
DiariZen MCP Server for SURE-EVAL.

MCP server implementation for speaker diarization using DiariZen.
Model: BUT-FIT/diarizen-wavlm-large-s80-md
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


class DiariZenServer:
    """MCP Server for DiariZen speaker diarization."""
    
    def __init__(self):
        self._initialized = False
        self._model = None
        self._model_path = os.environ.get(
            "MODEL_PATH", "BUT-FIT/diarizen-wavlm-large-s80-md"
        )
        self._device = os.environ.get("DEVICE", "auto")
        self._rttm_out_dir = os.environ.get("RTTM_OUT_DIR")
        
        # Tool definitions
        self._tools = [
            {
                "name": "diarize",
                "description": "Perform speaker diarization on audio file using DiariZen. Returns speaker segments in RTTM format.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "audio_path": {
                            "type": "string",
                            "description": "Path to the audio file to diarize"
                        },
                        "num_speakers": {
                            "type": "integer",
                            "description": "Exact number of speakers (optional, auto-detect if not provided)",
                            "minimum": 1
                        },
                        "min_speakers": {
                            "type": "integer",
                            "description": "Minimum number of speakers (optional)",
                            "minimum": 1
                        },
                        "max_speakers": {
                            "type": "integer",
                            "description": "Maximum number of speakers (optional)",
                            "minimum": 1
                        }
                    },
                    "required": ["audio_path"]
                }
            },
            {
                "name": "diarize_with_rttm",
                "description": "Diarize audio and save RTTM output to file. Returns speaker segments and saves RTTM format to specified directory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "audio_path": {
                            "type": "string",
                            "description": "Path to the audio file to diarize"
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Directory to save RTTM file",
                            "default": "./results"
                        },
                        "num_speakers": {
                            "type": "integer",
                            "description": "Exact number of speakers (optional)",
                            "minimum": 1
                        }
                    },
                    "required": ["audio_path"]
                }
            }
        ]
    
    def _load_model(self) -> None:
        """Lazy load model."""
        if self._model is not None:
            return
        
        try:
            from model import DiariZenModel
        except ImportError:
            # Try relative import
            sys.path.insert(0, str(Path(__file__).parent))
            from model import DiariZenModel
        
        print(f"Loading DiariZen model: {self._model_path}", file=sys.stderr)
        self._model = DiariZenModel(
            model_path=self._model_path,
            device=self._device,
            rttm_out_dir=self._rttm_out_dir,
        )
        print("Model loaded", file=sys.stderr)
    
    def run(self) -> None:
        """Run the server."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            
            try:
                request = json.loads(line)
                response = self._handle_request(request)
                if response:
                    self._send_response(response)
            except json.JSONDecodeError as e:
                self._send_error(None, -32700, f"Parse error: {e}")
            except Exception as e:
                request_id = request.get("id") if isinstance(request, dict) else None
                self._send_error(request_id, -32603, f"Internal error: {e}")
    
    def _handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Handle JSON-RPC request."""
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params", {})
        
        if method == "initialize":
            return self._handle_initialize(request_id, params)
        elif method == "notifications/initialized":
            return None
        elif method == "tools/list":
            return self._handle_tools_list(request_id)
        elif method == "tools/call":
            return self._handle_tools_call(request_id, params)
        elif method == "shutdown":
            return self._handle_shutdown(request_id)
        else:
            return self._error_response(request_id, -32601, f"Method not found: {method}")
    
    def _handle_initialize(self, request_id: Any, params: dict) -> dict[str, Any]:
        """Handle initialize request."""
        self._initialized = True
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "diarizen-server",
                    "version": "1.0.0"
                }
            }
        }
    
    def _handle_tools_list(self, request_id: Any) -> dict[str, Any]:
        """Handle tools/list request."""
        if not self._initialized:
            return self._error_response(request_id, -32001, "Server not initialized")
        
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": self._tools}
        }
    
    def _handle_tools_call(self, request_id: Any, params: dict) -> dict[str, Any]:
        """Handle tools/call request."""
        if not self._initialized:
            return self._error_response(request_id, -32001, "Server not initialized")
        
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        try:
            result = self._execute_tool(tool_name, arguments)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as e:
            error_msg = str(e)
            print(f"Tool execution error: {error_msg}", file=sys.stderr)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {error_msg}"}],
                    "isError": True,
                    "error": error_msg
                }
            }
    
    def _execute_tool(self, tool_name: str, arguments: dict) -> dict[str, Any]:
        """Execute a tool."""
        self._load_model()
        
        if tool_name == "diarize":
            return self._diarize(arguments)
        elif tool_name == "diarize_with_rttm":
            return self._diarize_with_rttm(arguments)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
    
    def _diarize(self, arguments: dict) -> dict[str, Any]:
        """Diarize audio."""
        audio_path = arguments.get("audio_path")
        num_speakers = arguments.get("num_speakers")
        min_speakers = arguments.get("min_speakers")
        max_speakers = arguments.get("max_speakers")
        
        if not audio_path:
            raise ValueError("audio_path is required")
        
        print(f"Diarizing: {audio_path}", file=sys.stderr)
        
        result = self._model.diarize(
            audio_path=audio_path,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        
        # Format segments for output
        segments_text = []
        for seg in result.segments:
            segments_text.append(f"{seg.start:.2f}s - {seg.end:.2f}s: {seg.speaker}")
        
        output_text = f"""Speaker Diarization Results:

Number of speakers detected: {result.num_speakers}

Segments:
{chr(10).join(segments_text) if segments_text else "No segments found"}

RTTM Format:
{result.rttm}
"""
        
        return {
            "content": [{"type": "text", "text": output_text}],
            "isError": False,
            "data": {
                "num_speakers": result.num_speakers,
                "segments": [
                    {"start": seg.start, "end": seg.end, "speaker": seg.speaker}
                    for seg in result.segments
                ],
                "rttm": result.rttm
            }
        }
    
    def _diarize_with_rttm(self, arguments: dict) -> dict[str, Any]:
        """Diarize audio and save RTTM."""
        audio_path = arguments.get("audio_path")
        output_dir = arguments.get("output_dir", "./results")
        num_speakers = arguments.get("num_speakers")
        
        if not audio_path:
            raise ValueError("audio_path is required")
        
        print(f"Diarizing with RTTM output: {audio_path}", file=sys.stderr)
        
        result = self._model.diarize_with_rttm_output(
            audio_path=audio_path,
            output_dir=output_dir,
            num_speakers=num_speakers,
        )
        
        # Get RTTM file path
        rttm_path = Path(output_dir) / f"{Path(audio_path).stem}.rttm"
        
        output_text = f"""Speaker Diarization Complete!

Number of speakers: {result.num_speakers}
RTTM saved to: {rttm_path}

RTTM Content:
{result.rttm}
"""
        
        return {
            "content": [{"type": "text", "text": output_text}],
            "isError": False,
            "data": {
                "num_speakers": result.num_speakers,
                "rttm_path": str(rttm_path),
                "rttm": result.rttm,
                "segments": [
                    {"start": seg.start, "end": seg.end, "speaker": seg.speaker}
                    for seg in result.segments
                ]
            }
        }
    
    def _handle_shutdown(self, request_id: Any) -> dict[str, Any]:
        """Handle shutdown request."""
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    
    def _error_response(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        """Create error response."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message}
        }
    
    def _send_response(self, response: dict[str, Any]) -> None:
        """Send response to stdout."""
        print(json.dumps(response), flush=True)
    
    def _send_error(self, request_id: Any, code: int, message: str) -> None:
        """Send error response."""
        self._send_response(self._error_response(request_id, code, message))


if __name__ == "__main__":
    server = DiariZenServer()
    server.run()
