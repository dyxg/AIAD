"""不依赖 locust 的烟雾测试脚本。

用途：
- 在没有压测意图的情况下快速验证后端起没起、各端点接得通
- 验证压测代码里的 payload 工厂返回的字段被后端接受
- 在本地 LLM/Chroma 不可用时也能跑（依赖外部资源的端点会被自动跳过或忽略 5xx）

用法：
    python stress_tests/smoke.py
    python stress_tests/smoke.py --host http://localhost:8000
    python stress_tests/smoke.py --skip-llm  --skip-heavy

退出码：
- 0：所有"必跑"端点（state-schema、tasks/recent）至少返回 2xx
- 1：必跑端点失败（说明后端没起或路由变更）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenarios._common import (  # noqa: E402
    PATH_AGENT_CONTEXT,
    PATH_AGENT_COPYWRITER,
    PATH_AGENT_RAG,
    PATH_AGENT_VISION,
    PATH_RECENT,
    PATH_RUN,
    PATH_STATE_SCHEMA,
    PATH_TASK,
    make_context_payload,
    make_copywriter_payload,
    make_rag_payload,
    make_run_payload,
    make_vision_payload,
)


class SmokeResult:
    def __init__(self, name: str, *, required: bool):
        self.name = name
        self.required = required
        self.status: int | None = None
        self.elapsed_ms: float = 0.0
        self.error: str | None = None

    def ok(self) -> bool:
        if self.error:
            return False
        if self.status is None:
            return False
        return 200 <= self.status < 500


async def _hit(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> tuple[int | None, float, str | None]:
    t0 = time.perf_counter()
    try:
        if method == "GET":
            resp = await client.get(path, timeout=timeout)
        else:
            resp = await client.post(path, json=json_body or {}, timeout=timeout)
        elapsed = (time.perf_counter() - t0) * 1000
        return resp.status_code, elapsed, None
    except httpx.HTTPError as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        return None, elapsed, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # 任何异常都不让脚本本身挂掉
        elapsed = (time.perf_counter() - t0) * 1000
        return None, elapsed, f"{type(exc).__name__}: {exc}"


async def run_smoke(
    host: str,
    *,
    skip_llm: bool,
    skip_heavy: bool,
) -> list[SmokeResult]:
    results: list[SmokeResult] = []

    async with httpx.AsyncClient(base_url=host, follow_redirects=True) as client:

        async def step(name: str, *, required: bool, coro) -> SmokeResult:
            r = SmokeResult(name, required=required)
            r.status, r.elapsed_ms, r.error = await coro
            results.append(r)
            return r

        # ------- 必跑：纯逻辑端点，不依赖 DB / LLM ------------------------------
        await step(
            "GET /agents/state-schema",
            required=True,
            coro=_hit(client, "GET", PATH_STATE_SCHEMA),
        )
        await step(
            "GET /tasks/recent?limit=5",
            required=True,
            coro=_hit(client, "GET", f"{PATH_RECENT}?limit=5"),
        )

        # ------- 任务相关：依赖 TaskStore，可能 404 但不应 5xx ------------------
        await step(
            "GET /task/{nonexistent}",
            required=False,
            coro=_hit(
                client, "GET", PATH_TASK.format(task_id="nonexistent_smoke"),
            ),
        )

        # ------- Agent 直调：依赖 Chroma / LLM ----------------------------------
        if not skip_llm:
            await step(
                "POST /agents/vision/run",
                required=False,
                coro=_hit(client, "POST", PATH_AGENT_VISION,
                          json_body=make_vision_payload()),
            )
            await step(
                "POST /agents/context/run",
                required=False,
                coro=_hit(client, "POST", PATH_AGENT_CONTEXT,
                          json_body=make_context_payload()),
            )
            await step(
                "POST /agents/copywriter/run",
                required=False,
                coro=_hit(client, "POST", PATH_AGENT_COPYWRITER,
                          json_body=make_copywriter_payload(), timeout=30.0),
            )

        # Agent 4 RAG 不调用 LLM，只查 Chroma；如果 Chroma 没初始化数据，
        # 后端有 cold-start 兜底（返回空列表），不会 5xx
        await step(
            "POST /agents/rag/run",
            required=False,
            coro=_hit(client, "POST", PATH_AGENT_RAG,
                      json_body=make_rag_payload()),
        )

        # ------- 主流水线：极昂贵，会启动后台任务（爬虫 + LangGraph）-----------
        if not skip_heavy:
            await step(
                "POST /run (smoke, limit=1)",
                required=False,
                coro=_hit(
                    client, "POST", PATH_RUN,
                    json_body=make_run_payload(limit=1, max_comments_per_note=1),
                    timeout=15.0,
                ),
            )

    return results


def _print_results(results: list[SmokeResult]) -> int:
    """返回 exit code。"""
    print()
    print("=" * 72)
    print(f"{'STATUS':<8}{'TIME(ms)':<12}{'REQ':<6}{'NAME'}")
    print("-" * 72)
    fail_required = 0
    for r in results:
        if r.error:
            status_str = "ERR"
        elif r.status is None:
            status_str = "----"
        else:
            status_str = str(r.status)
        flag = "[REQ]" if r.required else ""
        print(f"{status_str:<8}{r.elapsed_ms:<12.1f}{flag:<6}{r.name}")
        if r.error:
            print(f"        └─ {r.error}")
        if r.required and not r.ok():
            fail_required += 1
    print("=" * 72)
    if fail_required > 0:
        print(f"FAIL: {fail_required} required endpoint(s) did not return 2xx-4xx")
        return 1
    print("PASS: required endpoints reachable")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="后端压测前的烟雾测试（不依赖 locust）"
    )
    parser.add_argument(
        "--host", default="http://localhost:8000",
        help="目标后端 URL（不带尾斜杠）",
    )
    parser.add_argument(
        "--skip-llm", action="store_true",
        help="跳过会真打 LLM 的 agent 端点（vision/context/copywriter）",
    )
    parser.add_argument(
        "--skip-heavy", action="store_true",
        help="跳过 POST /run（会启动爬虫+LangGraph 全流程）",
    )
    args = parser.parse_args()

    print(f"[smoke] target host = {args.host}")
    print(f"[smoke] skip_llm    = {args.skip_llm}")
    print(f"[smoke] skip_heavy  = {args.skip_heavy}")
    print()

    results = asyncio.run(
        run_smoke(args.host, skip_llm=args.skip_llm, skip_heavy=args.skip_heavy)
    )
    return _print_results(results)


if __name__ == "__main__":
    raise SystemExit(main())
