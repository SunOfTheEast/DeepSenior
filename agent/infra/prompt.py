"""
Prompt loading for agent package.

Loads YAML prompt files from agent/{module}/prompts/{language}/{agent}.yaml.
"""

from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parent.parent  # agent/


class PromptManager:
    """Loads prompt YAML files for agents."""

    def load_prompts(
        self,
        module_name: str | None = None,
        agent_name: str | None = None,
        language: str = "zh",
        **kwargs,
    ) -> dict | None:
        if not module_name or not agent_name:
            return None
        prompt_path = _AGENT_ROOT / module_name / "prompts" / language / f"{agent_name}.yaml"
        if not prompt_path.exists():
            return None
        try:
            import yaml
            with open(prompt_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except ImportError:
            return _parse_simple_yaml(prompt_path)
        except Exception:
            return None


_prompt_manager: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptManager()
    return _prompt_manager


def _parse_simple_yaml(path: Path) -> dict:
    """Minimal YAML parser for prompt files (handles key: | multiline blocks)."""
    result = {}
    current_key = None
    current_lines: list[str] = []

    def _flush():
        nonlocal current_key, current_lines
        if current_key and current_lines:
            result[current_key] = "\n".join(current_lines).strip()
        current_key = None
        current_lines = []

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.rstrip()
        if stripped and not stripped[0].isspace() and ":" in stripped:
            _flush()
            key, _, val = stripped.partition(":")
            val = val.strip()
            if val in ("|", ">"):
                current_key = key.strip()
                current_lines = []
            elif val:
                result[key.strip()] = val
        elif current_key is not None:
            content = line[2:] if line.startswith("  ") else line
            current_lines.append(content)
    _flush()
    return result
