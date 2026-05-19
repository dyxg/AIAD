"""Locust 入口：聚合所有场景的 HttpUser 类。

启动方式（任选其一）：

    # 1) 启用 Web UI（推荐，团队 lead 可视化拖滑动条）
    locust -f stress_tests/locustfile.py --host http://localhost:8000

    # 2) 无 UI，命令行直接跑某个 conf
    locust -f stress_tests/locustfile.py --config stress_tests/conf/smoke.conf

    # 3) 只跑某一类场景
    locust -f stress_tests/locustfile.py --tags agent4
    locust -f stress_tests/locustfile.py --tags read,journey

筛选 Tag：
- read         - 所有读端点
- submit       - POST /run 提交动作
- journey      - 完整用户旅程
- sse          - 长连接 SSE
- agent2/3/4/5 - 单 Agent 直调
- llm          - 所有依赖 LLM 的 task（搭配 ``--exclude-tags llm`` 跳开）

权重在各 User 类自身定义（基于 ``LOCUST_SKIP_*`` env 自动归零），
缺什么外部资源就把对应 User 类的 weight 设为 0，Locust 不会 spawn 它。
默认权重比例：
- ReadOnlyUser           50  （读多写少）
- FullJourneyUser        15  （核心业务路径，受 LOCUST_SKIP_HEAVY_RUN 控制）
- Agent4RagUser          15  （RAG 热点，无 LLM 依赖）
- SubmitOnlyUser          5  （提交，受 LOCUST_SKIP_HEAVY_RUN 控制）
- SseListenerUser         5  （长连接，受 LOCUST_SKIP_SSE 控制）
- Agent2/3/5*User         3-4（依赖 LLM，受 LOCUST_SKIP_LLM 控制）

要在 UI 里手工挑选 User 类时，加 ``--class-picker``。
"""

from __future__ import annotations

# 必须 import 所有 User 类让 locust 自动发现。
# 各类的 weight 已经在各自模块里定义好（按 SKIP env 自动归零）。
from scenarios.agents_direct import (  # noqa: F401
    Agent2VisionUser,
    Agent3ContextUser,
    Agent4RagUser,
    Agent5CopywriterUser,
)
from scenarios.analyze_submit import SubmitOnlyUser  # noqa: F401
from scenarios.full_journey import FullJourneyUser  # noqa: F401
from scenarios.read_only import ReadOnlyUser  # noqa: F401
from scenarios.sse_listener import SseListenerUser  # noqa: F401
