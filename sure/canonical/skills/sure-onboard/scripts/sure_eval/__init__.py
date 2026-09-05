"""SURE-EVAL: Tool and Model Evaluation Framework (model-tool backend subset).

This is the stripped backend package shipped with the sure_onboard skill.
It contains only the model-tool surface: models/, inference/, protocols/.
The full framework's core/agent/datasets/evaluation packages are not
included here; importing this package does not eagerly load them.
"""

__version__ = "0.1.0"
