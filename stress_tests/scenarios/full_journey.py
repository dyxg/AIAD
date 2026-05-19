"""拟真用户旅程：提交 → 轮询 meta → 拉详情 → 拉洞察。

这是最贴近真实流量的场景：每个用户的行为是有状态的——
拿到 task_id 后才能查后续端点，不像 read_only 那样无脑随机命中。

⚠️ 由于会触发 POST /run（昂贵），默认通过 ``LOCUST_SUBMIT_LIMIT=1`` 控制。
真实压测时通常**不**用这个 user 类直接打高并发，而是用它产生的 task_id
去喂 ReadOnlyUser / SseListenerUser 做读侧压测。
"""

from __future__ import annotations

import logging
import os
import random
import time

from locust import HttpUser, between, tag, task

from ._common import (
    PATH_RUN,
    PATH_TASK,
    PATH_TASK_INSIGHTS,
    PATH_TASK_META,
    SKIP_HEAVY_RUN,
    make_run_payload,
)

logger = logging.getLogger(__name__)


_DEFAULT_LIMIT = int(os.getenv("LOCUST_SUBMIT_LIMIT", "1"))
_DEFAULT_MAX_COMMENTS = int(os.getenv("LOCUST_SUBMIT_MAX_COMMENTS", "1"))

# 单个 task 最长轮询多少次。轮询间隔由 wait_time 决定。
_MAX_POLLS = int(os.getenv("LOCUST_JOURNEY_MAX_POLLS", "10"))


class FullJourneyUser(HttpUser):
    """完整用户旅程：

    1. 提交分析（POST /run）
    2. 拿到 task_id 后轮询 meta 直到 status != running 或达到上限
    3. 拉一次完整 result（GET /task/{id}）
    4. 拉一次 insights（GET /task/{id}/insights）

    每个步骤都用 catch_response 显式上报，方便在 Locust UI 区分阶段延迟。
    """

    wait_time = between(2.0, 4.0)
    weight = 0 if SKIP_HEAVY_RUN else 15
    # 单个用户内部状态
    last_task_id: str | None = None

    @tag("journey")
    @task
    def full_flow(self) -> None:
        task_id = self._submit()
        if task_id is None:
            return
        self.last_task_id = task_id
        self._poll_meta(task_id)
        self._fetch_result(task_id)
        self._fetch_insights(task_id)

    # ------- 内部步骤 ----------------------------------------------------------

    def _submit(self) -> str | None:
        payload = make_run_payload(
            limit=_DEFAULT_LIMIT,
            max_comments_per_note=_DEFAULT_MAX_COMMENTS,
        )
        with self.client.post(
            PATH_RUN, json=payload,
            name="journey/1-submit POST /run",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"submit failed status={resp.status_code}")
                return None
            try:
                data = resp.json()
                task_id = str(data.get("task_id") or "")
            except Exception as exc:
                resp.failure(f"invalid json: {exc}")
                return None
            if not task_id:
                resp.failure("no task_id in response")
                return None
            resp.success()
            return task_id

    def _poll_meta(self, task_id: str) -> None:
        for i in range(_MAX_POLLS):
            time.sleep(random.uniform(0.5, 1.5))
            with self.client.get(
                PATH_TASK_META.format(task_id=task_id),
                name="journey/2-poll GET /task/{id}/meta",
                catch_response=True,
            ) as resp:
                if resp.status_code >= 500:
                    resp.failure(f"poll error {resp.status_code}")
                    return
                resp.success()
                try:
                    status = (resp.json() or {}).get("status", "")
                except Exception:
                    status = ""
                if status and status != "running":
                    return
        # 达到 max polls 还在 running，不算失败，但记录一下
        logger.debug("task %s still running after %d polls", task_id, _MAX_POLLS)

    def _fetch_result(self, task_id: str) -> None:
        with self.client.get(
            PATH_TASK.format(task_id=task_id),
            name="journey/3-result GET /task/{id}",
            catch_response=True,
        ) as resp:
            if resp.status_code >= 500:
                resp.failure(f"result error {resp.status_code}")
            else:
                resp.success()

    def _fetch_insights(self, task_id: str) -> None:
        with self.client.get(
            PATH_TASK_INSIGHTS.format(task_id=task_id),
            name="journey/4-insights GET /task/{id}/insights",
            catch_response=True,
        ) as resp:
            if resp.status_code == 409:
                # 任务还没成功，409 是预期行为
                resp.success()
            elif resp.status_code >= 500:
                resp.failure(f"insights error {resp.status_code}")
            else:
                resp.success()
