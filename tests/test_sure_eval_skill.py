from pathlib import Path

import yaml


SKILL_DIR = Path(__file__).parents[1] / "skills" / "sure-eval"


def test_skill_has_valid_frontmatter_and_agent_metadata() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\n")
    _, frontmatter, body = skill.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "sure-eval"
    assert metadata["description"]
    assert "readiness" in body

    ui = yaml.safe_load((SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    assert ui["interface"]["display_name"] == "SURE-EVAL"
    assert ui["interface"]["default_prompt"]
