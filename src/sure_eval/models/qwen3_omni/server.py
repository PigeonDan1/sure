#!/usr/bin/env python3
"""
Qwen3-Omni MCP Server for SURE-EVAL.

MCP server for Qwen3-Omni API via DashScope.
Supports text and audio generation.
"""

from __future__ import annotations

import json
import os
import sys
import base64
from pathlib import Path
from typing import Any


class Qwen3OmniServer:
    """MCP Server for Qwen3-Omni API."""
    
    def __init__(self):
        self._initialized = False
        self._client = None
        
        # API configuration from environment
        self._api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        self._base_url = os.environ.get(
            "DASHSCOPE_BASE_URL", 
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        )
        self._default_model = os.environ.get("DEFAULT_MODEL", "qwen3-omni-flash")
        self._default_voice = os.environ.get("DEFAULT_VOICE", "Cherry")
        self._default_audio_format = os.environ.get("DEFAULT_AUDIO_FORMAT", "wav")
        self._default_sample_rate = int(os.environ.get("DEFAULT_SAMPLE_RATE", "24000"))
        
        # Tool definitions
        self._tools = [
            {
                "name": "omni_chat",
                "description": "Send a chat message to Qwen3-Omni and get text + optional audio response",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The text message to send"
                        },
                        "generate_audio": {
                            "type": "boolean",
                            "description": "Whether to generate audio response",
                            "default": True
                        },
                        "voice": {
                            "type": "string",
                            "description": "Voice to use for audio (e.g., 'Cherry')",
                            "default": "Cherry"
                        },
                        "output_audio_path": {
                            "type": "string",
                            "description": "Path to save the generated audio file",
                            "default": ""
                        }
                    },
                    "required": ["message"]
                }
            },
            {
                "name": "omni_chat_text_only",
                "description": "Send a chat message to Qwen3-Omni and get text response only (faster)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The text message to send"
                        }
                    },
                    "required": ["message"]
                }
            }
        ]
    
    def _get_client(self):
        """Lazy initialize OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as e:
                raise RuntimeError(f"Missing openai package: {e}") from e
            
            if not self._api_key:
                raise RuntimeError(
                    "DASHSCOPE_API_KEY not set. Please provide API key."
                )
            
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
            )
        return self._client
    
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
                    "name": "qwen3-omni-server",
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
        if tool_name == "omni_chat":
            return self._omni_chat(arguments, generate_audio=True)
        elif tool_name == "omni_chat_text_only":
            return self._omni_chat(arguments, generate_audio=False)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
    
    def _omni_chat(self, arguments: dict, generate_audio: bool) -> dict[str, Any]:
        """Call Qwen3-Omni API."""
        import numpy as np
        
        message = arguments.get("message", "")
        voice = arguments.get("voice", self._default_voice)
        output_audio_path = arguments.get("output_audio_path", "")
        
        if not message:
            raise ValueError("message is required")
        
        client = self._get_client()
        
        print(f"Calling Qwen3-Omni: message='{message[:50]}...', audio={generate_audio}", file=sys.stderr)
        
        # Build request
        request_params = {
            "model": self._default_model,
            "messages": [{"role": "user", "content": message}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        
        if generate_audio:
            request_params["modalities"] = ["text", "audio"]
            request_params["audio"] = {
                "voice": voice,
                "format": self._default_audio_format
            }
        
        try:
            completion = client.chat.completions.create(**request_params)
            
            # Process streaming response
            text_response = ""
            audio_base64 = ""
            
            for chunk in completion:
                if chunk.choices and chunk.choices[0].delta.content:
                    text_response += chunk.choices[0].delta.content
                
                if (chunk.choices and 
                    hasattr(chunk.choices[0].delta, "audio") and 
                    chunk.choices[0].delta.audio):
                    audio_base64 += chunk.choices[0].delta.audio.get("data", "")
            
            # Save audio if generated
            audio_saved_path = None
            if generate_audio and audio_base64 and output_audio_path:
                wav_bytes = base64.b64decode(audio_base64)
                audio_np = np.frombuffer(wav_bytes, dtype=np.int16)
                
                import soundfile as sf
                sf.write(output_audio_path, audio_np, samplerate=self._default_sample_rate)
                audio_saved_path = output_audio_path
                print(f"Audio saved to: {output_audio_path}", file=sys.stderr)
            
            # Build response
            result_text = text_response
            if audio_saved_path:
                result_text += f"\n\n[Audio saved to: {audio_saved_path}]"
            elif generate_audio and audio_base64:
                result_text += "\n\n[Audio generated but not saved (no output path provided)]"
            
            return {
                "content": [{"type": "text", "text": result_text}],
                "isError": False
            }
            
        except Exception as e:
            print(f"API call failed: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            raise RuntimeError(f"API call failed: {e}") from e
    
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
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
    
    def _send_error(self, request_id: Any, code: int, message: str) -> None:
        """Send error response."""
        self._send_response(self._error_response(request_id, code, message))


def main():
    """Main entry point."""
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    
    server = Qwen3OmniServer()
    server.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
