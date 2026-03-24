#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RouterAgent - 教学策略路由器

两种决策：
1. decide_after_grading()  - 批改后决策（规则驱动，无 LLM，同步）
2. evaluate_checkpoint()   - 评估学生是否通过了当前 checkpoint（LLM，无状态）

Skill化要点：
  evaluate_checkpoint 不再接收 TutorSession，而是接受显式 context。
  调用方（TutorManager 等）负责从 session 中提取所需字段再传入。
  这使得 Research/Progress 模块可以在没有 TutorSession 的情况下直接调用此 skill。
"""

from agent.base_agent import BaseAgent
from agent.utils import safe_parse_json
from ..data_structures import (
    Checkpoint,
    CheckpointEvaluation,
    ErrorType,
    GraderResult,
    GranularityLevel,
    RouterDecision,
    TutorMode,
)


class RouterAgent(BaseAgent):
    """路由器：决定何时批改、何时引导、何时推进 checkpoint"""

    _ROUTING_RULES: dict[ErrorType, tuple[TutorMode, bool, bool]] = {
        ErrorType.CORRECT:          (TutorMode.GRADING,  False, False),
        ErrorType.COMPUTATIONAL:    (TutorMode.GRADING,  False, True),
        ErrorType.MISCONCEPTION:    (TutorMode.GRADING,  False, True),
        ErrorType.INCOMPLETE:       (TutorMode.GRADING,  False, True),
        ErrorType.NO_ATTEMPT:       (TutorMode.SOCRATIC, True,  False),
        ErrorType.ON_TRACK_STUCK:   (TutorMode.SOCRATIC, True,  False),
        ErrorType.WRONG_PATH_MINOR: (TutorMode.SOCRATIC, True,  False),
        ErrorType.WRONG_PATH_MAJOR: (TutorMode.SOCRATIC, True,  False),
    }

    _GRANULARITY_MAP: dict[ErrorType, GranularityLevel] = {
        ErrorType.NO_ATTEMPT:       GranularityLevel.COARSE,
        ErrorType.ON_TRACK_STUCK:   GranularityLevel.FINE,
        ErrorType.WRONG_PATH_MINOR: GranularityLevel.MEDIUM,
        ErrorType.WRONG_PATH_MAJOR: GranularityLevel.COARSE,
    }

    def __init__(
        self,
        api_key: str,
        base_url: str,
        language: str = "zh",
        api_version: str | None = None,
        binding: str = "openai",
    ):
        super().__init__(
            module_name="tutor",
            agent_name="router_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    # -------------------------------------------------------------------------
    # Skill 1: route_decision（同步，规则驱动）
    # -------------------------------------------------------------------------

    def decide_after_grading(self, grader_result: GraderResult) -> RouterDecision:
        """
        批改后的路由决策（纯规则，不调用 LLM）

        Skill 签名：(grader_result: GraderResult) -> RouterDecision
        """
        error_type = grader_result.error_type
        mode, needs_new_plan, deliver_feedback = self._ROUTING_RULES[error_type]
        granularity = self._GRANULARITY_MAP.get(error_type, GranularityLevel.MEDIUM)

        return RouterDecision(
            mode=mode,
            reason=f"错误类型：{error_type.value}",
            needs_new_plan=needs_new_plan,
            suggested_granularity=granularity,
            deliver_grader_feedback=deliver_feedback,
            reset_from_error=grader_result.error_description if needs_new_plan else None,
        )

    # -------------------------------------------------------------------------
    # Skill 2: evaluate_checkpoint（LLM，无状态）
    # -------------------------------------------------------------------------

    async def evaluate_checkpoint(
        self,
        checkpoint: Checkpoint,
        passed_checkpoints_history: str,
        student_response: str,
        total_checkpoints: int | str = "?",
        interaction_context: str = "",
    ) -> CheckpointEvaluation:
        """
        评估学生是否通过了当前 checkpoint（无状态版本）

        Skill 签名：
            (checkpoint, passed_checkpoints_history, student_response,
             total_checkpoints, interaction_context)
            -> CheckpointEvaluation

        Args:
            checkpoint: 当前 checkpoint 对象
            passed_checkpoints_history: 已通过 checkpoint 的格式化描述
                （由调用方从 session 提取，或离线分析时手动构造）
            student_response: 学生的回答
            total_checkpoints: 总 checkpoint 数（用于显示进度）
            interaction_context: 最近的对话上下文（含提交和导师引导），
                帮助评估器判断学生是否在提交或前序对话中已完成当前目标
        """
        system_prompt = self.get_prompt("system")
        eval_template = self.get_prompt("evaluate_checkpoint")

        if not system_prompt or not eval_template:
            passed = len(student_response.strip()) > 15
            return CheckpointEvaluation(
                checkpoint_passed=passed,
                student_understanding=student_response[:50],
                next_hint_level=min(checkpoint.hint_level + 1, 3),
                reason="Fallback: response length heuristic",
            )

        user_prompt = eval_template.format(
            checkpoint_index=checkpoint.index + 1,
            total_checkpoints=total_checkpoints,
            checkpoint_description=checkpoint.description,
            guiding_question=checkpoint.guiding_question,
            student_response=student_response,
            passed_checkpoints_history=passed_checkpoints_history,
            interaction_context=interaction_context or "（无上下文）",
        )

        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.1,
            stage="checkpoint_eval",
        )

        data = self._parse_json(response)

        regressed = data.get("regressed_to_checkpoint")
        regressed_to = regressed if isinstance(regressed, int) and regressed < checkpoint.index else None

        return CheckpointEvaluation(
            checkpoint_passed=data.get("checkpoint_passed", False),
            student_understanding=data.get("student_understanding", ""),
            next_hint_level=data.get("next_hint_level", min(checkpoint.hint_level + 1, 3)),
            reason=data.get("reason", ""),
            regressed_to_checkpoint=regressed_to,
            used_alternative_method=data.get("used_alternative_method", False),
            alternative_method_name=data.get("alternative_method_name"),
        )

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _parse_json(self, response: str) -> dict:
        result = safe_parse_json(response)
        if not result:
            self.logger.warning(f"RouterAgent: failed to parse JSON: {response[:200]}")
        return result
