"""SSE 长连接压测场景。

为什么单独写：
- Locust 的 ``self.client.get()`` 拿到响应后立即关闭连接，**无法**真实模拟 SSE 长连接
- 我们用 ``requests`` 的 ``stream=True`` 手动持有连接、逐事件读取，并把每次事件作为
  独立的 Locust event 上报，这样就能在 Web UI 看到"按事件计延迟分布"

测试目的：
- 后端能同时维持多少个 SSE 连接
- 长连接下 ``task_event_queues`` 字典是否正常 GC
- ``await asyncio.sleep(0.1)`` 的协程占用是否瓶颈

⚠️ 真实跑此场景前，需要先有 task_id。两种姿势：
- 推荐：先开一个 SubmitOnlyUser，跑产生一批 task_id 写入 ``LOCUST_TASK_IDS`` 环境变量
- 简化：直接用占位 task_id，端点会立即返回 ``done`` / ``error``，仍然能压"建立连接"动作
"""

from __future__ import annotations

import logging
import os
import random
import time
from typing import Any

from locust import HttpUser, between, events, tag, task

from ._common import PATH_TASK_STREAM, SKIP_SSE

logger = logging.getLogger(__name__)


_PLACEHOLDER_TASK_IDS = [
    "sse_bench_001",
    "sse_bench_002",
    "sse_bench_003",
]


def _pick_task_id() -> str:
    raw = os.getenv("LOCUST_TASK_IDS", "").strip()
    if raw:
        ids = [t.strip() for t in raw.split(",") if t.strip()]
        if ids:
            return random.choice(ids)
    return random.choice(_PLACEHOLDER_TASK_IDS)


# 单条事件超过该上限就主动断开，避免 worker 协程被卡住
_MAX_EVENTS_PER_CONN = int(os.getenv("LOCUST_SSE_MAX_EVENTS", "100"))
# 单条 SSE 连接最长保持秒数
_MAX_SECONDS_PER_CONN = int(os.getenv("LOCUST_SSE_MAX_SECONDS", "30"))


class SseListenerUser(HttpUser):
    """SSE 监听用户：建立连接，逐事件读，记录每次事件抵达的延迟。"""

    # 上一个连接断开后随机停 1-3s 再开下一个
    wait_time = between(1.0, 3.0)
    weight = 0 if SKIP_SSE else 5

    @tag("sse", "stream")
    @task
    def listen_stream(self) -> None:
        task_id = _pick_task_id()
        url = PATH_TASK_STREAM.format(task_id=task_id)
        # 用底层 session 拿 stream=True；保留 Locust 上报机制
        connect_start = time.perf_counter()
        try:
            with self.client.get(
                url,
                name="GET /task/{id}/stream (connect)",
                stream=True,
                catch_response=True,
                timeout=_MAX_SECONDS_PER_CONN + 5,
            ) as resp:
                connect_ms = (time.perf_counter() - connect_start) * 1000
                if resp.status_code != 200:
                    if resp.status_code == 404:
                        # 占位 task_id，后端会立即返回 done/error 也算合法
                        resp.success()
                    else:
                        resp.failure(f"unexpected status {resp.status_code}")
                    return
                resp.success()
                self._consume_events(resp.raw, task_id, connect_ms)
        except Exception as exc:
            logger.warning("sse listen error task_id=%s: %s", task_id, exc)

    def _consume_events(self, raw_stream: Any, task_id: str, connect_ms: float) -> None:
        """逐事件消费 SSE，按事件单独 fire 上报。"""
        deadline = time.perf_counter() + _MAX_SECONDS_PER_CONN
        events_seen = 0
        last_event_at = time.perf_counter()
        try:
            for line in raw_stream:
                if events_seen >= _MAX_EVENTS_PER_CONN:
                    break
                if time.perf_counter() >= deadline:
                    break
                if not line:
                    continue
                if isinstance(line, bytes):
                    text = line.decode("utf-8", errors="replace")
                else:
                    text = str(line)
                text = text.strip()
                if not text or not text.startswith("data:"):
                    continue
                # 上报"两条事件之间的间隔"作为响应时间，能在 Locust 图里看到
                # SSE 事件抵达节奏
                now = time.perf_counter()
                gap_ms = (now - last_event_at) * 1000
                last_event_at = now
                events_seen += 1
                events.request.fire(
                    request_type="SSE",
                    name="event-gap /task/{id}/stream",
                    response_time=gap_ms,
                    response_length=len(text),
                    exception=None,
                    context={"task_id": task_id},
                )
                # 简单识别 done/error 字样就主动断开
                if '"type": "done"' in text or '"type": "error"' in text:
                    break
        except Exception as exc:
            logger.warning("sse event-loop error task_id=%s: %s", task_id, exc)
