#!/usr/bin/env python3
"""
Example script for testing DashScope integration with SURE-EVAL.

This script demonstrates:
1. Using DashScope as a tool for evaluation
2. Using the Orchestrator Agent for autonomous evaluation
3. Comparing DashScope with other tools
"""

import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sure_eval.agent.orchestrator import OrchestratorAgent, InteractiveOrchestrator
from sure_eval.tools.dashscope_adapter import DashScopeToolWrapper
from sure_eval.agent.evaluator import AutonomousEvaluator


API_KEY = "sk-f8ae3fc37bdd4953977e813f77b7324f"


def test_dashscope_as_tool():
    """Test DashScope as a standalone tool."""
    print("=" * 60)
    print("Test 1: DashScope as Tool")
    print("=" * 60)
    
    # Create DashScope tool wrapper
    tool = DashScopeToolWrapper(
        name="dashscope_qwen",
        api_key=API_KEY,
        model="qwen-audio-asr",
    )
    
    # Test transcription
    # Note: You need an actual audio file for this test
    test_audio = "path/to/test/audio.wav"
    
    if os.path.exists(test_audio):
        result = tool.invoke(test_audio, task="ASR")
        print(f"Transcription: {result.get('text')}")
        print(f"Success: {result.get('success')}")
    else:
        print(f"Skipping audio test (file not found: {test_audio})")
        print("To test, provide a valid audio file path.")
    
    print()


def test_orchestrator_agent():
    """Test the Orchestrator Agent with LLM."""
    print("=" * 60)
    print("Test 2: Orchestrator Agent")
    print("=" * 60)
    
    agent = OrchestratorAgent(api_key=API_KEY)
    
    # Test direct evaluation (no LLM)
    print("\nTesting direct evaluation...")
    print("This would evaluate a tool on a dataset.")
    print("Note: Requires actual tools and datasets to be configured.")
    
    # Example of how to use:
    # result = agent.evaluate_direct("dashscope_qwen", "aishell1", max_samples=10)
    
    print()


def test_chat_interface():
    """Test chat interface with LLM."""
    print("=" * 60)
    print("Test 3: Chat Interface")
    print("=" * 60)
    
    agent = OrchestratorAgent(api_key=API_KEY)
    
    # Test simple chat (no tool calls)
    print("\nUser: List available datasets")
    response = agent.chat("List available datasets", stream=False)
    
    if response["type"] == "complete":
        print(f"Assistant: {response['content']}")
    
    print()


def test_batch_evaluation():
    """Test batch evaluation workflow."""
    print("=" * 60)
    print("Test 4: Batch Evaluation Workflow")
    print("=" * 60)
    
    print("""
Workflow for batch evaluation:

1. Configure datasets in config/default.yaml
2. Register tools in config/mcp_tools.yaml  
3. Run evaluation:

   from sure_eval.agent.orchestrator import OrchestratorAgent
   
   agent = OrchestratorAgent(api_key=API_KEY)
   
   # Direct evaluation
   result = agent.evaluate_direct(
       tool_name="dashscope_qwen",
       dataset="aishell1",
       max_samples=100,
   )
   
   # Batch evaluation
   results = agent.evaluator.batch_evaluate(
       tool_name="dashscope_qwen",
       datasets=["aishell1", "librispeech_clean"],
       max_samples=100,
   )
   
   # Compare with other tools
   comparison = agent.evaluator.compare_tools(
       tool_names=["dashscope_qwen", "whisper", "wenet"],
       dataset="aishell1",
   )

4. View results:
   
   recommendation = agent.evaluator.recommend_tool("aishell1")
   print(f"Best tool: {recommendation['best_tool']}")
    """)
    
    print()


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("SURE-EVAL DashScope Integration Tests")
    print("=" * 60)
    print()
    
    # Check API key
    if not API_KEY or API_KEY == "your-api-key":
        print("WARNING: Please set your DashScope API key!")
        print("Edit this file and replace API_KEY with your actual key.")
        print()
    
    # Run tests
    test_dashscope_as_tool()
    test_orchestrator_agent()
    test_chat_interface()
    test_batch_evaluation()
    
    print("=" * 60)
    print("Tests completed!")
    print("=" * 60)
    print()
    print("To run interactive mode:")
    print("  from sure_eval.agent.orchestrator import InteractiveOrchestrator")
    print("  orchestrator = InteractiveOrchestrator(api_key=API_KEY)")
    print("  orchestrator.run()")
    print()


if __name__ == "__main__":
    main()
