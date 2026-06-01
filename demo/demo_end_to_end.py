#!/usr/bin/env python3
"""
End-to-End Integration Test for SURE-EVAL.

This script demonstrates that all components work together:
1. Data Pipeline - Dataset discovery and loading
2. Tool Management - Model registry and MCP client
3. Evaluation - Metrics computation and RPS calculation
4. Reports - SOTA baselines and model performance tracking
5. Agent - Orchestration of the complete flow

Usage:
    python demo/demo_end_to_end.py

Expected output should show all checks passing with ✅
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

console = Console()


def test_data_pipeline():
    """Test data pipeline components."""
    console.print("\n[bold blue]1. Testing Data Pipeline[/bold blue]")
    
    from sure_eval.core.config import Config
    from sure_eval.datasets import DatasetManager
    
    # Test Config
    config = Config.from_env()
    assert config.data.root == "./data", "Config data root mismatch"
    console.print("  ✅ Config loaded")
    
    # Test DatasetManager
    dm = DatasetManager(config)
    datasets = dm.list_available()
    assert len(datasets) > 0, "No datasets available"
    console.print(f"  ✅ DatasetManager: {len(datasets)} datasets available")
    
    # Test normalization
    normalized = dm.normalize_dataset_name("CS_dialogue")
    assert normalized == "cs_dialogue", f"Normalization failed: {normalized}"
    console.print(f"  ✅ Dataset normalization: CS_dialogue → cs_dialogue")
    
    # Test baseline lookup with normalized name
    baseline = config.get_baseline("aishell1")
    assert baseline is not None, "Baseline not found for aishell1"
    assert baseline.score == 0.80, f"Wrong baseline score: {baseline.score}"
    console.print(f"  ✅ Baseline lookup: aishell1 CER = {baseline.score}%")
    
    return True


def test_tool_management():
    """Test tool management components."""
    console.print("\n[bold blue]2. Testing Tool Management[/bold blue]")
    
    from sure_eval.models import ModelRegistry, get_benchmark_name
    from sure_eval.tools.mcp_client import ToolRegistry
    
    # Test ModelRegistry
    mr = ModelRegistry()
    models = mr.list_models()
    console.print(f"  ✅ ModelRegistry: {len(models)} models discovered")
    
    # Test ToolRegistry
    tr = ToolRegistry()
    tools = tr.list_tools()
    console.print(f"  ✅ ToolRegistry: {len(tools)} tools configured")
    
    # Test model mapping
    benchmark = get_benchmark_name("asr_qwen3")
    assert benchmark == "Qwen3-ASR-1.7B", f"Wrong mapping: {benchmark}"
    console.print(f"  ✅ Model mapping: asr_qwen3 → {benchmark}")
    
    return True


def test_evaluation_system():
    """Test evaluation components."""
    console.print("\n[bold blue]3. Testing Evaluation System[/bold blue]")
    
    from sure_eval.evaluation import SUREEvaluator, RPSManager
    
    # Test SUREEvaluator
    evaluator = SUREEvaluator(language="zh")
    assert evaluator is not None, "Evaluator initialization failed"
    console.print("  ✅ SUREEvaluator initialized")
    
    # Test RPSManager
    rps_mgr = RPSManager()
    
    # Test RPS calculation
    # For aishell1: CER SOTA = 0.80, score = 0.80 → RPS = 1.0
    rps = rps_mgr.calculator.calculate("aishell1", 0.80)
    assert rps is not None, "RPS calculation failed"
    assert abs(rps - 1.0) < 0.01, f"Wrong RPS: {rps}"
    console.print(f"  ✅ RPS calculation: CER 0.80 → RPS {rps:.2f}")
    
    # Test RPS with worse score
    rps_half = rps_mgr.calculator.calculate("aishell1", 1.60)
    assert abs(rps_half - 0.5) < 0.01, f"Wrong RPS for half: {rps_half}"
    console.print(f"  ✅ RPS calculation: CER 1.60 → RPS {rps_half:.2f}")
    
    return True


def test_report_system():
    """Test report system components."""
    console.print("\n[bold blue]4. Testing Report System[/bold blue]")
    
    from sure_eval.reports import SOTAManager, ReportManager
    
    # Test SOTAManager
    sota = SOTAManager()
    datasets = sota.list_datasets()
    assert len(datasets) >= 14, f"Not enough SOTA baselines: {len(datasets)}"
    console.print(f"  ✅ SOTAManager: {len(datasets)} SOTA baselines loaded")
    
    # Test baseline retrieval
    baseline = sota.get_baseline("aishell1")
    assert baseline is not None, "aishell1 baseline not found"
    assert baseline.metric == "cer", f"Wrong metric: {baseline.metric}"
    assert baseline.sota_model == "Qwen3-Omni", f"Wrong SOTA model: {baseline.sota_model}"
    console.print(f"  ✅ SOTA baseline: aishell1 CER = {baseline.score}% by {baseline.sota_model}")
    
    # Test ReportManager
    reports = ReportManager()
    models = reports.list_models()
    assert len(models) >= 7, f"Not enough models in reports: {len(models)}"
    console.print(f"  ✅ ReportManager: {len(models)} model reports loaded")
    
    # Test leaderboard
    results = reports.get_results_for_dataset("aishell1")
    assert len(results) > 0, "No results for aishell1"
    top_model, top_result = results[0]
    console.print(f"  ✅ Leaderboard: {top_model} is #1 on aishell1 (RPS {top_result.rps:.2f})")
    
    return True


def test_agent_integration():
    """Test agent orchestration."""
    console.print("\n[bold blue]5. Testing Agent Integration[/bold blue]")
    
    from sure_eval.agent.evaluator import AutonomousEvaluator
    from sure_eval.models import get_benchmark_name
    
    # Test AutonomousEvaluator initialization
    ae = AutonomousEvaluator()
    assert ae.dataset_manager is not None, "DatasetManager not initialized"
    assert ae.tool_registry is not None, "ToolRegistry not initialized"
    assert ae.rps_manager is not None, "RPSManager not initialized"
    assert ae.sota_manager is not None, "SOTAManager not initialized"
    assert ae.report_manager is not None, "ReportManager not initialized"
    console.print("  ✅ AutonomousEvaluator initialized with all components")
    
    # Test integration: Dataset → Normalization → SOTA lookup
    datasets = ae.dataset_manager.list_available()
    sample_ds = datasets[0]  # e.g., 'aishell1'
    normalized = ae.dataset_manager.normalize_dataset_name(sample_ds)
    baseline = ae.sota_manager.get_baseline(normalized)
    assert baseline is not None, f"No baseline for {normalized}"
    console.print(f"  ✅ Data→SOTA: {sample_ds} → {normalized} → {baseline.sota_model}")
    
    # Test integration: Tool → Benchmark mapping
    benchmark = get_benchmark_name("asr_qwen3")
    report = ae.report_manager.get_model(benchmark)
    assert report is not None, f"No report for {benchmark}"
    console.print(f"  ✅ Tool→Report: asr_qwen3 → {benchmark} → Report loaded")
    
    return True


def test_complete_flow():
    """Test a complete evaluation flow (without actual inference)."""
    console.print("\n[bold blue]6. Testing Complete Flow[/bold blue]")
    
    from sure_eval.agent.evaluator import AutonomousEvaluator
    from sure_eval.models import get_benchmark_name
    
    ae = AutonomousEvaluator()
    
    # Simulate evaluation flow
    tool_name = "asr_qwen3"
    dataset = "aishell1"
    score = 0.85  # Simulated CER
    
    # Step 1: Normalize dataset
    normalized_ds = ae.dataset_manager.normalize_dataset_name(dataset)
    console.print(f"  1️⃣ Dataset normalization: {dataset} → {normalized_ds}")
    
    # Step 2: Get benchmark name
    benchmark = get_benchmark_name(tool_name)
    console.print(f"  2️⃣ Tool mapping: {tool_name} → {benchmark}")
    
    # Step 3: Lookup SOTA
    baseline = ae.sota_manager.get_baseline(normalized_ds)
    sota_score = baseline.score
    sota_model = baseline.sota_model
    console.print(f"  3️⃣ SOTA lookup: {normalized_ds} SOTA = {sota_score}% ({sota_model})")
    
    # Step 4: Calculate RPS
    rps = ae.sota_manager.calculate_rps(normalized_ds, score)
    console.print(f"  4️⃣ RPS calculation: Score {score}% → RPS {rps:.2f}")
    
    # Step 5: Check if SOTA
    is_sota = (benchmark == sota_model) if benchmark else False
    console.print(f"  5️⃣ SOTA check: {benchmark} vs {sota_model} → {'✅ SOTA' if is_sota else '❌ Not SOTA'}")
    
    assert rps is not None, "RPS calculation failed"
    assert rps < 1.0, f"Expected RPS < 1.0 for score {score} > SOTA {sota_score}"
    console.print(f"  ✅ Complete flow successful: RPS = {rps:.2f}")
    
    return True


def main():
    """Run all integration tests."""
    console.print("\n" + "=" * 60)
    console.print("[bold green]SURE-EVAL End-to-End Integration Test[/bold green]")
    console.print("=" * 60)
    
    tests = [
        ("Data Pipeline", test_data_pipeline),
        ("Tool Management", test_tool_management),
        ("Evaluation System", test_evaluation_system),
        ("Report System", test_report_system),
        ("Agent Integration", test_agent_integration),
        ("Complete Flow", test_complete_flow),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, success, None))
        except Exception as e:
            results.append((name, False, str(e)))
            import traceback
            console.print(f"[red]{traceback.format_exc()}[/red]")
    
    # Summary
    console.print("\n" + "=" * 60)
    console.print("[bold]Test Summary[/bold]")
    console.print("=" * 60)
    
    table = Table()
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Error", style="red")
    
    all_passed = True
    for name, success, error in results:
        status = "✅ PASS" if success else "❌ FAIL"
        error_msg = error or ""
        table.add_row(name, status, error_msg)
        if not success:
            all_passed = False
    
    console.print(table)
    
    if all_passed:
        console.print("\n[bold green]🎉 All integration tests passed![/bold green]")
        console.print("[dim]The SURE-EVAL framework is fully operational.[/dim]\n")
        return 0
    else:
        console.print("\n[bold red]⚠️ Some tests failed. Check errors above.[/bold red]\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
