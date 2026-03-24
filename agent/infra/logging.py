"""
Logging utilities for agent package.

Provides get_logger and LLMStats without external src.* dependency.
"""

import logging
from dataclasses import dataclass, field


def get_logger(name: str, log_dir: str | None = None) -> logging.Logger:
    """Get a named logger with basic config."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("[%(name)s] %(levelname)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


@dataclass
class LLMStats:
    """Lightweight LLM call statistics tracker."""

    module_name: str = ""
    total_calls: int = 0
    total_input_chars: int = 0
    total_output_chars: int = 0
    _calls: list[dict] = field(default_factory=list, repr=False)

    def add_call(
        self,
        model: str = "",
        system_prompt: str = "",
        user_prompt: str = "",
        response: str = "",
        **kwargs,
    ) -> None:
        self.total_calls += 1
        self.total_input_chars += len(system_prompt) + len(user_prompt)
        self.total_output_chars += len(response)
        self._calls.append(
            {"model": model, "input_len": len(system_prompt) + len(user_prompt), "output_len": len(response)}
        )

    def reset(self) -> None:
        self.total_calls = 0
        self.total_input_chars = 0
        self.total_output_chars = 0
        self._calls.clear()

    def print_summary(self) -> None:
        print(
            f"[{self.module_name}] LLM calls={self.total_calls}, "
            f"input_chars={self.total_input_chars}, output_chars={self.total_output_chars}"
        )
