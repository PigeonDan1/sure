#!/usr/bin/env python3
"""
Test script for ASR Whisper model.
"""

import sys
from pathlib import Path

def test_import():
    """Test model import."""
    try:
        from model import ASRWhisperModel, TranscriptionResult
        print("✓ Model import successful")
        return True
    except Exception as e:
        print(f"✗ Model import failed: {e}")
        return False

def test_mcp_server():
    """Test MCP server initialization."""
    import subprocess
    import json
    
    try:
        proc = subprocess.Popen(
            ['.venv/bin/python', 'server.py'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Test initialize
        proc.stdin.write(json.dumps({
            'jsonrpc': '2.0', 'id': 1, 
            'method': 'initialize', 'params': {}
        }) + '\n')
        proc.stdin.flush()
        
        resp = json.loads(proc.stdout.readline())
        assert 'result' in resp, "Initialize failed"
        assert resp['result']['serverInfo']['name'] == 'asr-whisper-server'
        
        # Test tools/list
        proc.stdin.write(json.dumps({
            'jsonrpc': '2.0', 'id': 2,
            'method': 'tools/list', 'params': {}
        }) + '\n')
        proc.stdin.flush()
        
        resp = json.loads(proc.stdout.readline())
        assert 'result' in resp, "Tools list failed"
        tools = resp['result']['tools']
        assert len(tools) == 1, f"Expected 1 tool, got {len(tools)}"
        assert tools[0]['name'] == 'asr_transcribe'
        
        proc.terminate()
        proc.wait(timeout=1)
        
        print("✓ MCP server test passed")
        return True
        
    except Exception as e:
        print(f"✗ MCP server test failed: {e}")
        return False

def main():
    """Run all tests."""
    print("=" * 50)
    print("ASR Whisper Model Tests")
    print("=" * 50)
    
    results = []
    results.append(("Import", test_import()))
    results.append(("MCP Server", test_mcp_server()))
    
    print("=" * 50)
    passed = sum(1 for _, r in results if r)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
