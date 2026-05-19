"""单 Agent 直调压测场景。

每个 Agent 端点单独成 task，可以通过 ``-T <tag>`` 选择性压测某一个 Agent：

    locust -f stress_tests/locustfile.py -T agent4
    locust -f stress_tests/locustfile.py -T agent3

Tag 命名约定：
- agent2 -> vision
- agent3 -> context (NLP)
- agent4 -> rag (Chroma 检索，不打 LLM)
- agent5 -> copywriter

⚠️ vision/context/copywriter 需要真实 LLM API key，本地无 key 时会 5xx 或
fallback。设置 ``LOCUST_SKIP_LLM=1`` 即可在 task 启动时跳过这三类。
RAG（Agent 4）只查 Chroma，理论上无 LLM 依赖。
"""

from __future__ import annotations

import logging

from locust import HttpUser, between, tag, task

from ._common import (
    PATH_AGENT_CONTEXT,
    PATH_AGENT_COPYWRITER,
    PATH_AGENT_RAG,
    PATH_AGENT_VISION,
    SKIP_LLM,
    make_context_payload,
    make_copywriter_payload,
    make_rag_payload,
    make_vision_payload,
)

logger = logging.getLogger(__name__)


def _expect_success(resp, name: str, *, allow_5xx_when_skipped: bool = True) -> None:
    """判 200 算 OK。

    LLM 端点在缺 API key 时通常 5xx，对压测来说算"基础设施缺失"，
    我们用 SKIP_LLM 显式跳过；走到这里仍然 5xx 视为真错。
    """
    if resp.status_code == 200:
        resp.success()
        return
    if resp.status_code == 422:
        resp.failure(f"validation error on {name}: {resp.text[:200]}")
        return
    if resp.status_code >= 500:
        resp.failure(f"server error {resp.status_code} on {name}")
        return
    resp.success()


class Agent2VisionUser(HttpUser):
    """Agent2 视觉分析压测。
    依赖：vision LLM API key + media 文件能被读取（不存在则走 fallback）。
    """

    wait_time = between(0.5, 2.0)
    weight = 0 if SKIP_LLM else 3

    @tag("agent2", "vision", "llm")
    @task
    def run_vision(self) -> None:
        with self.client.post(
            PATH_AGENT_VISION,
            json=make_vision_payload(),
            name="POST /agents/vision/run",
            catch_response=True,
            timeout=60,
        ) as resp:
            _expect_success(resp, "vision")


class Agent3ContextUser(HttpUser):
    """Agent3 评论语境分析压测。依赖：LLM API key。"""

    wait_time = between(0.5, 2.0)
    weight = 0 if SKIP_LLM else 3

    @tag("agent3", "context", "llm")
    @task
    def run_context(self) -> None:
        with self.client.post(
            PATH_AGENT_CONTEXT,
            json=make_context_payload(),
            name="POST /agents/context/run",
            catch_response=True,
            timeout=60,
        ) as resp:
            _expect_success(resp, "context")


class Agent4RagUser(HttpUser):
    """Agent4 RAG 检索压测。**不依赖 LLM**，只查 Chroma 向量库。

    这是 Agent 4 owner 重点关心的端点。
    """

    wait_time = between(0.2, 1.0)
    weight = 15

    @tag("agent4", "rag", "chroma")
    @task(3)
    def run_rag_default(self) -> None:
        """默认 top_k = 随机 3/5/8。"""
        with self.client.post(
            PATH_AGENT_RAG,
            json=make_rag_payload(),
            name="POST /agents/rag/run",
            catch_response=True,
            timeout=15,
        ) as resp:
            _expect_success(resp, "rag")

    @tag("agent4", "rag", "chroma")
    @task(1)
    def run_rag_top10(self) -> None:
        """top_k=10 极限值，验证大返回集是否拖慢序列化。"""
        with self.client.post(
            PATH_AGENT_RAG,
            json=make_rag_payload(top_k=10),
            name="POST /agents/rag/run (top_k=10)",
            catch_response=True,
            timeout=15,
        ) as resp:
            _expect_success(resp, "rag-top10")


class Agent5CopywriterUser(HttpUser):
    """Agent5 文案生成压测。依赖：LLM API key（最贵的端点之一）。"""

    wait_time = between(1.0, 3.0)
    weight = 0 if SKIP_LLM else 4

    @tag("agent5", "copywriter", "llm")
    @task
    def run_copywriter(self) -> None:
        with self.client.post(
            PATH_AGENT_COPYWRITER,
            json=make_copywriter_payload(),
            name="POST /agents/copywriter/run",
            catch_response=True,
            timeout=120,
        ) as resp:
            _expect_success(resp, "copywriter")
