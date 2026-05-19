"""读类压测场景：模拟前端高频轮询、列表刷新、schema 拉取。

目标：
- 衡量纯读端点的吞吐与 p95 延迟
- 不写入任何数据，与生产数据隔离
- 一个 ReadOnlyUser 启动后随机命中下列端点之一，按权重分布

权重设计依据：
- /tasks/recent 是侧栏列表，前端 ~10s/次
- /task/{id}/* 三连是详情页打开后的轮询，更高频
- /agents/state-schema 仅 dev 调试用，权重低
"""

from __future__ import annotations

import logging
import random

from locust import HttpUser, between, tag, task

from ._common import (
    PATH_RECENT,
    PATH_STATE_SCHEMA,
    PATH_TASK,
    PATH_TASK_INSIGHTS,
    PATH_TASK_META,
)

logger = logging.getLogger(__name__)


# 模拟用户已经从某次提交得到的 task_id 池。
# 真实压测时由 LOCUST_TASK_IDS 环境变量注入；缺失时用占位值，后端会 404，
# 但端点本身的解析/权限/IO 路径仍然被压上。
_PLACEHOLDER_TASK_IDS = [
    "bench_task_001",
    "bench_task_002",
    "bench_task_003",
    "bench_task_004",
    "bench_task_005",
]


def _pick_task_id() -> str:
    import os

    raw = os.getenv("LOCUST_TASK_IDS", "").strip()
    if raw:
        ids = [t.strip() for t in raw.split(",") if t.strip()]
        if ids:
            return random.choice(ids)
    return random.choice(_PLACEHOLDER_TASK_IDS)


class ReadOnlyUser(HttpUser):
    """读密集型用户：完全不写入。

    适合验证：
    - 前端高频轮询场景下后端能撑多少 RPS
    - JSON 文件读 + Pydantic 序列化的 CPU 开销
    - 多用户并发读 TaskStore 时是否有锁竞争

    每次操作之间 sleep 1-3 秒，模拟前端轮询节奏。
    """

    # 单用户两次操作之间随机 sleep 1-3 秒。压测真实并发用户数请通过 -u 参数控制。
    wait_time = between(1.0, 3.0)
    weight = 50

    @tag("read", "schema")
    @task(1)
    def get_state_schema(self) -> None:
        """拉全局状态模板。纯 Pydantic dump，最轻。"""
        with self.client.get(
            PATH_STATE_SCHEMA, name="GET /agents/state-schema",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"unexpected status {resp.status_code}")

    @tag("read", "list")
    @task(5)
    def list_recent_tasks(self) -> None:
        """侧栏列表刷新。压 TaskStore 全量扫的开销。"""
        limit = random.choice([5, 10, 20])
        with self.client.get(
            f"{PATH_RECENT}?limit={limit}",
            name="GET /tasks/recent",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"unexpected status {resp.status_code}")

    @tag("read", "detail")
    @task(8)
    def get_task_meta(self) -> None:
        """任务详情 meta（前端打开任务页第一眼）。"""
        task_id = _pick_task_id()
        # 占位 ID 会 404，是允许的；其它 5xx 才算压测失败
        with self.client.get(
            PATH_TASK_META.format(task_id=task_id),
            name="GET /task/{id}/meta",
            catch_response=True,
        ) as resp:
            if resp.status_code >= 500:
                resp.failure(f"server error {resp.status_code}")
            else:
                resp.success()

    @tag("read", "detail")
    @task(6)
    def get_task_result(self) -> None:
        """任务详情 result。"""
        task_id = _pick_task_id()
        with self.client.get(
            PATH_TASK.format(task_id=task_id),
            name="GET /task/{id}",
            catch_response=True,
        ) as resp:
            if resp.status_code >= 500:
                resp.failure(f"server error {resp.status_code}")
            else:
                resp.success()

    @tag("read", "insights")
    @task(4)
    def get_task_insights(self) -> None:
        """任务洞察页（情感分布 / 词云 / review queue）。

        ⚠️ 这是读端点里**最重的**：要计算情感、构造词云、聚合多张表。
        预期延迟显著高于其它读端点。
        """
        task_id = _pick_task_id()
        with self.client.get(
            PATH_TASK_INSIGHTS.format(task_id=task_id),
            name="GET /task/{id}/insights",
            catch_response=True,
        ) as resp:
            if resp.status_code >= 500:
                resp.failure(f"server error {resp.status_code}")
            else:
                resp.success()
