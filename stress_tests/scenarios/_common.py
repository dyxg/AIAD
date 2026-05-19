"""压测公共组件：路径常量、payload 工厂、随机数据池、环境变量解析。

设计原则：
- 所有可变参数都从环境变量读，不硬编码
- payload 工厂返回的字典直接喂给 ``self.client.post(json=...)``
- 数据池一次加载、跨用户复用，避免重复 IO
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any

# === 路径常量 =================================================================

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_AD_TYPES_FILE = _DATA_DIR / "ad_types.txt"
_SAMPLE_COMMENTS_FILE = _DATA_DIR / "sample_comments.json"
_SAMPLE_STATE_FILE = _DATA_DIR / "sample_state.json"

# === API 路径前缀 =============================================================

API_PREFIX = "/api/ad-intel"

PATH_RUN = f"{API_PREFIX}/run"
PATH_TASK = f"{API_PREFIX}/task/{{task_id}}"
PATH_TASK_META = f"{API_PREFIX}/task/{{task_id}}/meta"
PATH_TASK_INSIGHTS = f"{API_PREFIX}/task/{{task_id}}/insights"
PATH_TASK_STREAM = f"{API_PREFIX}/task/{{task_id}}/stream"
PATH_RECENT = f"{API_PREFIX}/tasks/recent"
PATH_STATE_SCHEMA = f"{API_PREFIX}/agents/state-schema"
PATH_AGENT_VISION = f"{API_PREFIX}/agents/vision/run"
PATH_AGENT_CONTEXT = f"{API_PREFIX}/agents/context/run"
PATH_AGENT_RAG = f"{API_PREFIX}/agents/rag/run"
PATH_AGENT_COPYWRITER = f"{API_PREFIX}/agents/copywriter/run"

# === 环境变量开关 =============================================================


def env_flag(name: str, default: bool = False) -> bool:
    """读 ``LOCUST_<NAME>=1/true/yes`` 等真值。"""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


SKIP_LLM = env_flag("LOCUST_SKIP_LLM", default=False)
SKIP_SSE = env_flag("LOCUST_SKIP_SSE", default=False)
SKIP_HEAVY_RUN = env_flag("LOCUST_SKIP_HEAVY_RUN", default=False)


# === 数据池（懒加载、单例）====================================================


_AD_TYPES: list[str] | None = None
_SAMPLE_COMMENTS: list[dict[str, Any]] | None = None
_SAMPLE_STATE: dict[str, Any] | None = None


def get_ad_types() -> list[str]:
    """加载广告类型词池（小红书/抖音常见品类）。"""
    global _AD_TYPES
    if _AD_TYPES is None:
        try:
            text = _AD_TYPES_FILE.read_text(encoding="utf-8")
            _AD_TYPES = [line.strip() for line in text.splitlines() if line.strip()]
        except Exception:
            _AD_TYPES = ["防晒霜", "口红", "面膜"]
    return _AD_TYPES


def get_sample_comments() -> list[dict[str, Any]]:
    global _SAMPLE_COMMENTS
    if _SAMPLE_COMMENTS is None:
        try:
            _SAMPLE_COMMENTS = json.loads(
                _SAMPLE_COMMENTS_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            _SAMPLE_COMMENTS = [
                {"user": "u1", "content": "好用", "likes": 10},
                {"user": "u2", "content": "一般", "likes": 5},
            ]
    return _SAMPLE_COMMENTS


def get_sample_state() -> dict[str, Any]:
    global _SAMPLE_STATE
    if _SAMPLE_STATE is None:
        try:
            _SAMPLE_STATE = json.loads(
                _SAMPLE_STATE_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            _SAMPLE_STATE = {
                "vision_analysis": {},
                "nlp_analysis": {},
            }
    return _SAMPLE_STATE


# === payload 工厂 =============================================================


def make_run_payload(
    *,
    ad_type: str | None = None,
    enable_media_download: bool = False,
    limit: int = 10,
    max_comments_per_note: int = 5,
) -> dict[str, Any]:
    """构造 ``POST /run`` 入参。"""
    chosen_ad = ad_type or random.choice(get_ad_types())
    return {
        "ad_type": chosen_ad,
        "post_url": "",
        "product_info": chosen_ad,
        "target_style": random.choice(["测评风", "种草风", "教程风", "对比风"]),
        "keywords": [chosen_ad],
        "platform": "xhs",
        "limit": limit,
        "max_comments_per_note": max_comments_per_note,
        "enable_media_download": enable_media_download,
        "time_range": "",
    }


def make_vision_payload() -> dict[str, Any]:
    """``POST /agents/vision/run`` 入参。

    注意：``media_paths`` 指向不存在的本地文件时，VisionAgent 会走 fallback。
    我们故意构造一个不存在的路径，避免依赖测试环境真实文件。
    """
    return {
        "media_paths": [
            f"data/raw/stub_{random.randint(1, 9999)}.jpg",
        ]
    }


def make_context_payload() -> dict[str, Any]:
    """``POST /agents/context/run`` 入参。"""
    pool = get_sample_comments()
    sample_size = min(len(pool), random.randint(3, 8))
    return {
        "comments": random.sample(pool, sample_size),
        "product_info": random.choice(get_ad_types()),
    }


def make_rag_payload(top_k: int | None = None) -> dict[str, Any]:
    """``POST /agents/rag/run`` 入参。"""
    state = get_sample_state()
    return {
        "vision_analysis": state.get("vision_analysis", {}),
        "nlp_analysis": state.get("nlp_analysis", {}),
        "top_k": top_k or random.choice([3, 5, 8]),
    }


def make_copywriter_payload() -> dict[str, Any]:
    """``POST /agents/copywriter/run`` 入参。"""
    state = get_sample_state()
    ad = random.choice(get_ad_types())
    return {
        "request_info": {
            "post_url": "",
            "product_info": ad,
            "target_style": "测评风",
        },
        "vision_analysis": state.get("vision_analysis", {}),
        "nlp_analysis": state.get("nlp_analysis", {}),
        "rag_references": [
            "真实体验：清透不闷痘",
            "性价比：百元内防晒霜首选",
            "便携：通勤包随手补涂",
        ],
        "styles": ["测评风", "种草风"],
    }
