# -*- coding: utf-8 -*-
"""DELETED: standalone multi-AI arbitration module (designer 2026-07-24).

3-AI collaboration lives inside auto_trade_strategy_creation_factory
and auto_trade_ai_consensus; this independent arbitrator is retired.
"""
DELETED = True


def arbitrate_reviews(*args, **kwargs):
    raise RuntimeError("ai_arbitration_module_deleted")


def call_with_retries(*args, **kwargs):
    raise RuntimeError("ai_arbitration_module_deleted")


def provider_order():
    return ("deepseek", "qwen", "glm")


def apply_grade_penalty(*args, **kwargs):
    return None
