"""提交分析场景：高频 ``POST /run``，纯压"提交"动作。

⚠️ 重要：每次 POST /run 后端都会启动后台线程做爬虫+LangGraph+LLM。
一段时间内大量提交会：
- 占满后台 ThreadPool（Python 默认）
- 把 inflight_keywords 字典撑大
- 文件系统并发写 ``data/processed/<task_id>_normalized.json``

本场景默认通过 ``LOCUST_SUBMIT_LIMIT`` 控制每次提交的爬取规模（默认 1 条），
否则真实跑爬虫会把测试拖死。
"""

from __future__ import annotations

import logging
import os

from locust import HttpUser, between, tag, task

from ._common import PATH_RUN, SKIP_HEAVY_RUN, make_run_payload

logger = logging.getLogger(__name__)


_DEFAULT_LIMIT = int(os.getenv("LOCUST_SUBMIT_LIMIT", "1"))
_DEFAULT_MAX_COMMENTS = int(os.getenv("LOCUST_SUBMIT_MAX_COMMENTS", "1"))


class SubmitOnlyUser(HttpUser):
    """只提交、不轮询的用户。压"任务创建"通路本身。

    用于：
    - 衡量 POST /run 的入口吞吐（含路由、Pydantic 校验、TaskStore 写入）
    - 检测同关键词并发提交时 inflight_keywords 去重逻辑是否正确
    - 暴露后台线程池满载后的退化行为
    """

    wait_time = between(2.0, 5.0)
    # 若 LOCUST_SKIP_HEAVY_RUN=1，Locust 不会 spawn 此 User 类
    weight = 0 if SKIP_HEAVY_RUN else 5

    @tag("submit", "heavy")
    @task(8)
    def submit_random(self) -> None:
        """随机品类，模拟正常用户分布。"""
        payload = make_run_payload(
            limit=_DEFAULT_LIMIT,
            max_comments_per_note=_DEFAULT_MAX_COMMENTS,
        )
        with self.client.post(
            PATH_RUN, json=payload, name="POST /run (random)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code in (422,):
                # 422 = 入参不合法。我们的工厂应该总返回合法入参，所以应当报告
                resp.failure(f"unexpected validation error: {resp.text[:200]}")
            elif resp.status_code >= 500:
                resp.failure(f"server error {resp.status_code}: {resp.text[:200]}")
            else:
                resp.success()

    @tag("submit", "dedup")
    @task(2)
    def submit_same_keyword(self) -> None:
        """所有用户都提交同一个关键词，专门压 in-flight 去重逻辑。

        预期行为：第一个请求触发爬虫，其它请求等待 / 复用缓存。
        如果服务端没去重，后端会重复爬同一关键词 → 资源浪费 → 在压测里表现为
        延迟暴涨而非 5xx。
        """
        same_kw = "防晒霜"
        payload = make_run_payload(
            ad_type=same_kw,
            limit=_DEFAULT_LIMIT,
            max_comments_per_note=_DEFAULT_MAX_COMMENTS,
        )
        with self.client.post(
            PATH_RUN, json=payload, name="POST /run (same-keyword)",
            catch_response=True,
        ) as resp:
            if resp.status_code >= 500:
                resp.failure(f"server error {resp.status_code}")
            else:
                resp.success()
