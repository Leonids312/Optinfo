import sys
from types import ModuleType, SimpleNamespace

import pytest

from optinfo import editorial, harness


def test_harness_reads_current_skill_for_each_request(monkeypatch, tmp_path):
    policy = tmp_path / "SKILL.md"
    policy.write_text("## 生成规则\n优先解释工艺变化。\n## 维护与核验\n维护说明", encoding="utf-8")
    monkeypatch.setattr(editorial, "POLICY_PATH", policy)
    monkeypatch.setattr(harness, "is_configured", lambda: True)
    captured = []

    class Harness:
        def __init__(self, **kwargs):
            captured.append(kwargs["env"]["DSH_SYSTEM_PROMPT"])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def run(self, *args, **kwargs):
            return SimpleNamespace(finish_reason="completed", final_response="结果")

    sdk = ModuleType("deepseek_harness")
    sdk.DeepSeekHarness = Harness
    monkeypatch.setitem(sys.modules, "deepseek_harness", sdk)
    assert harness._run("分析", tmp_path / "run-1") == "结果"
    policy.write_text("## 生成规则\n优先解释制造成本。\n## 维护与核验\n维护说明", encoding="utf-8")
    assert harness._run("回答", tmp_path / "run-2") == "结果"
    assert "优先解释工艺变化" in captured[0] and "优先解释制造成本" not in captured[0]
    assert "优先解释制造成本" in captured[1] and "优先解释工艺变化" not in captured[1]
    assert all("维护说明" not in value for value in captured)


def test_missing_policy_is_not_silently_replaced_by_outdated_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(editorial, "POLICY_PATH", tmp_path / "missing.md")
    with pytest.raises(FileNotFoundError):
        editorial.content_policy()
