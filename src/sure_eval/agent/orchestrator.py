"""Orchestrator Agent using Alibaba Cloud Bailian (DashScope) API."""

from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from sure_eval.core.config import Config
from sure_eval.core.logging import get_logger
from sure_eval.agent.evaluator import AutonomousEvaluator, EvaluationResult
from sure_eval.datasets.downloader import DatasetDownloader
from sure_eval.evaluation.rps import RPSManager

logger = get_logger(__name__)


class ToolCall:
    """Represents a tool call from the LLM."""
    
    def __init__(self, id: str, name: str, arguments: dict) -> None:
        self.id = id
        self.name = name
        self.arguments = arguments


class OrchestratorAgent:
    """
    Central orchestrator agent using DashScope API.
    
    Capabilities:
    1. Understand evaluation tasks
    2. Plan execution steps
    3. Call tools (download, evaluate, compute metrics)
    4. Report results
    """
    
    SYSTEM_PROMPT = """You are SURE-EVAL Orchestrator, an intelligent agent for evaluating audio processing tools and models.

Your job is to help users evaluate tools on benchmark datasets by:
1. Understanding the evaluation requirements
2. Planning the execution steps
3. Calling appropriate tools
4. Reporting comprehensive results

Available tools:
- download_dataset: Download a dataset
- evaluate_tool: Run evaluation on a tool
- compute_metrics: Compute metrics from predictions
- get_dataset_info: Get dataset information
- list_datasets: List available datasets
- list_tools: List available tools
- recommend_tool: Recommend best tool for a dataset

When evaluating, always:
1. Check if dataset exists, download if needed
2. Run the evaluation
3. Report metrics and RPS scores
4. Provide recommendations

Think step by step and use tools as needed."""

    def __init__(self, api_key: str | None = None, config: Config | None = None) -> None:
        self.config = config or Config.from_env()
        
        # Initialize DashScope client
        self.client = OpenAI(
            api_key=api_key or os.getenv("DASHSCOPE_API_KEY", ""),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        
        # Initialize components
        self.evaluator = AutonomousEvaluator(config)
        self.rps_manager = RPSManager(config)
        
        # Tool definitions for LLM
        self.tools = self._get_tool_definitions()
        
        logger.info("OrchestratorAgent initialized")
    
    def _get_tool_definitions(self) -> list[dict]:
        """Get tool definitions for function calling."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "download_dataset",
                    "description": "Download a dataset for evaluation",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "dataset": {
                                "type": "string",
                                "description": "Dataset name (e.g., 'aishell1', 'librispeech_clean')",
                            },
                        },
                        "required": ["dataset"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "evaluate_tool",
                    "description": "Evaluate a tool on a dataset",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tool_name": {
                                "type": "string",
                                "description": "Name of the tool to evaluate",
                            },
                            "dataset": {
                                "type": "string",
                                "description": "Dataset name",
                            },
                            "max_samples": {
                                "type": "integer",
                                "description": "Maximum samples to evaluate (default: all)",
                            },
                            "metric_type": {
                                "type": "string",
                                "description": "Metric type (cer, wer, bleu, accuracy, etc.)",
                            },
                        },
                        "required": ["tool_name", "dataset"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_dataset_info",
                    "description": "Get information about a dataset",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "dataset": {
                                "type": "string",
                                "description": "Dataset name",
                            },
                        },
                        "required": ["dataset"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_datasets",
                    "description": "List available datasets",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "recommend_tool",
                    "description": "Recommend the best tool for a dataset",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "dataset": {
                                "type": "string",
                                "description": "Dataset name",
                            },
                        },
                        "required": ["dataset"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "batch_evaluate",
                    "description": "Evaluate a tool on multiple datasets",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tool_name": {
                                "type": "string",
                                "description": "Name of the tool",
                            },
                            "datasets": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of dataset names",
                            },
                            "max_samples": {
                                "type": "integer",
                                "description": "Maximum samples per dataset",
                            },
                        },
                        "required": ["tool_name", "datasets"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "compare_tools",
                    "description": "Compare multiple tools on a dataset",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tool_names": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of tool names",
                            },
                            "dataset": {
                                "type": "string",
                                "description": "Dataset name",
                            },
                            "max_samples": {
                                "type": "integer",
                                "description": "Maximum samples",
                            },
                        },
                        "required": ["tool_names", "dataset"],
                    },
                },
            },
        ]
    
    def chat(
        self,
        user_input: str,
        conversation_history: list[dict] | None = None,
        model: str = "qwen-plus",
        stream: bool = True,
    ) -> dict[str, Any]:
        """
        Chat with the orchestrator agent.
        
        Args:
            user_input: User's request
            conversation_history: Previous messages
            model: Model to use (qwen-plus, qwen-max, etc.)
            stream: Whether to stream the response
            
        Returns:
            Response with results
        """
        messages = conversation_history or []
        messages.append({"role": "user", "content": user_input})
        
        logger.info("Sending request to LLM", model=model, user_input=user_input[:100])
        
        # First call - let the model decide what to do
        response = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                *messages,
            ],
            tools=self.tools,
            tool_choice="auto",
            stream=False,
        )
        
        assistant_message = response.choices[0].message
        
        # Check if there are tool calls
        if assistant_message.tool_calls:
            # Execute tool calls
            tool_results = self._execute_tool_calls(assistant_message.tool_calls)
            
            # Add assistant message and tool results to conversation
            messages.append({
                "role": "assistant",
                "content": assistant_message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in assistant_message.tool_calls
                ],
            })
            
            for tool_call_id, result in tool_results.items():
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            
            # Second call - get final response
            final_response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    *messages,
                ],
                stream=stream,
            )
            
            if stream:
                return {"type": "stream", "response": final_response}
            else:
                return {
                    "type": "complete",
                    "content": final_response.choices[0].message.content,
                    "tool_results": tool_results,
                }
        
        # No tool calls needed
        if stream:
            return {"type": "stream", "response": response}
        else:
            return {
                "type": "complete",
                "content": assistant_message.content,
            }
    
    def _execute_tool_calls(self, tool_calls: list) -> dict[str, Any]:
        """Execute tool calls and return results."""
        results = {}
        
        for tool_call in tool_calls:
            tool_id = tool_call.id
            tool_name = tool_call.function.name
            arguments = json.loads(tool_call.function.arguments)
            
            logger.info("Executing tool", tool=tool_name, args=arguments)
            
            try:
                result = self._execute_tool(tool_name, arguments)
                results[tool_id] = {
                    "status": "success",
                    "result": result,
                }
            except Exception as e:
                logger.error("Tool execution failed", tool=tool_name, error=str(e))
                results[tool_id] = {
                    "status": "error",
                    "error": str(e),
                }
        
        return results
    
    def _execute_tool(self, tool_name: str, arguments: dict) -> Any:
        """Execute a single tool."""
        if tool_name == "download_dataset":
            dataset = arguments["dataset"]
            path = self.evaluator.dataset_downloader.download(dataset)
            return {"dataset": dataset, "path": str(path), "status": "downloaded"}
        
        elif tool_name == "evaluate_tool":
            result = self.evaluator.evaluate_tool(
                tool_name=arguments["tool_name"],
                dataset=arguments["dataset"],
                max_samples=arguments.get("max_samples"),
                metric_type=arguments.get("metric_type"),
            )
            return {
                "tool_name": result.tool_name,
                "dataset": result.dataset,
                "metric": result.metric,
                "score": result.score,
                "rps": result.rps,
                "num_samples": result.num_samples,
                "duration": result.duration,
            }
        
        elif tool_name == "batch_evaluate":
            results = self.evaluator.batch_evaluate(
                tool_name=arguments["tool_name"],
                datasets=arguments["datasets"],
                max_samples=arguments.get("max_samples"),
            )
            return {
                "results": [
                    {
                        "dataset": r.dataset,
                        "score": r.score,
                        "rps": r.rps,
                    }
                    for r in results
                ],
            }
        
        elif tool_name == "compare_tools":
            comparison = self.evaluator.compare_tools(
                tool_names=arguments["tool_names"],
                dataset=arguments["dataset"],
                max_samples=arguments.get("max_samples"),
            )
            return comparison
        
        elif tool_name == "get_dataset_info":
            info = self.evaluator.dataset_downloader.get_dataset_info(arguments["dataset"])
            return info or {"error": "Dataset not found"}
        
        elif tool_name == "list_datasets":
            datasets = self.evaluator.dataset_downloader.list_datasets()
            return {"datasets": datasets}
        
        elif tool_name == "recommend_tool":
            recommendation = self.evaluator.recommend_tool(arguments["dataset"])
            return recommendation
        
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
    
    def evaluate_direct(
        self,
        tool_name: str,
        dataset: str,
        max_samples: int | None = None,
        verbose: bool = True,
    ) -> EvaluationResult:
        """
        Direct evaluation without LLM orchestration.
        
        This is a shortcut for quick evaluations.
        """
        logger.info("Direct evaluation", tool=tool_name, dataset=dataset)
        
        # Check if dataset exists
        if not self.evaluator.dataset_downloader.is_downloaded(dataset):
            if verbose:
                print(f"Dataset '{dataset}' not found. Downloading...")
            self.evaluator.dataset_downloader.download(dataset)
        
        # Run evaluation
        result = self.evaluator.evaluate_tool(tool_name, dataset, max_samples)
        
        if verbose:
            print(f"\n{'='*60}")
            print(f"Evaluation Result: {tool_name} on {dataset}")
            print(f"{'='*60}")
            print(f"Metric: {result.metric}")
            print(f"Score: {result.score:.4f}")
            print(f"RPS: {result.rps:.4f}" if result.rps else "RPS: N/A")
            print(f"Samples: {result.num_samples}")
            print(f"Duration: {result.duration:.2f}s")
            print(f"{'='*60}")
        
        return result


class InteractiveOrchestrator:
    """Interactive CLI for the orchestrator agent."""
    
    def __init__(self, api_key: str | None = None) -> None:
        self.agent = OrchestratorAgent(api_key=api_key)
        self.conversation_history: list[dict] = []
    
    def run(self) -> None:
        """Run interactive session."""
        print("=" * 60)
        print("SURE-EVAL Interactive Orchestrator")
        print("Powered by Alibaba Cloud Bailian (DashScope)")
        print("=" * 60)
        print("\nCommands:")
        print("  /eval <tool> <dataset> [n]  - Direct evaluation")
        print("  /datasets                   - List datasets")
        print("  /recommend <dataset>        - Get tool recommendation")
        print("  /clear                      - Clear conversation")
        print("  /quit                       - Exit")
        print()
        
        while True:
            try:
                user_input = input("You: ").strip()
                
                if not user_input:
                    continue
                
                if user_input == "/quit":
                    print("Goodbye!")
                    break
                
                if user_input == "/clear":
                    self.conversation_history = []
                    print("Conversation cleared.")
                    continue
                
                if user_input == "/datasets":
                    datasets = self.agent.evaluator.dataset_downloader.list_datasets()
                    print(f"\nAvailable datasets: {', '.join(datasets)}\n")
                    continue
                
                if user_input.startswith("/recommend "):
                    dataset = user_input.split(maxsplit=1)[1]
                    rec = self.agent.recommend_tool(dataset)
                    print(f"\nRecommendation for {dataset}:")
                    if rec["best_tool"]:
                        print(f"  Best tool: {rec['best_tool']} (RPS: {rec['best_rps']:.4f})")
                    else:
                        print(f"  Suggested: {rec.get('suggested_tool', 'None')}")
                    print()
                    continue
                
                if user_input.startswith("/eval "):
                    parts = user_input.split()
                    if len(parts) >= 3:
                        tool_name = parts[1]
                        dataset = parts[2]
                        max_samples = int(parts[3]) if len(parts) > 3 else None
                        self.agent.evaluate_direct(tool_name, dataset, max_samples)
                    else:
                        print("Usage: /eval <tool> <dataset> [max_samples]")
                    continue
                
                # Regular chat with LLM
                print("\nOrchestrator: ", end="", flush=True)
                
                response = self.agent.chat(
                    user_input,
                    conversation_history=self.conversation_history,
                    stream=False,
                )
                
                if response["type"] == "complete":
                    print(response["content"])
                    self.conversation_history.append({
                        "role": "assistant",
                        "content": response["content"],
                    })
                
                print()
                
            except KeyboardInterrupt:
                print("\nGoodbye!")
                break
            except Exception as e:
                print(f"Error: {e}")
