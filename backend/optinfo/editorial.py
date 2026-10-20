"""Read the same versioned preference skill used to maintain this project."""
from pathlib import Path


POLICY_PATH = Path(__file__).resolve().parents[2] / "skills" / "optinfo-content" / "SKILL.md"


def content_policy() -> str:
    text = POLICY_PATH.read_text(encoding="utf-8")
    start = text.index("## 生成规则")
    end = text.index("## 维护与核验", start)
    policy = text[start:end].strip()
    if not policy or len(policy) > 16000:
        raise ValueError("Optinfo content policy is missing or too large")
    return policy
