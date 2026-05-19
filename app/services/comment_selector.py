from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.services.normalize import parse_cn_number

SELECTION_STRATEGY_VERSION = "priority-comments-v1"


@dataclass(frozen=True)
class CommentSelectionConfig:
    """评论优先级策略配置，后续调权重/意图词时优先改这里或传入新配置。"""

    strategy_version: str = SELECTION_STRATEGY_VERSION
    limit: int = 50
    first_round_per_post: int = 1
    post_score_fields: tuple[str, ...] = (
        "like_count",
        "comment_count",
        "collect_count",
        "share_count",
    )
    comment_like_fields: tuple[str, ...] = ("like_count", "likes")
    intent_keywords: tuple[str, ...] = field(default_factory=lambda: DEFAULT_INTENT_KEYWORDS)


DEFAULT_INTENT_KEYWORDS: tuple[str, ...] = (
    "求链接",
    "求推荐",
    "求",
    "链接",
    "推荐",
    "好用吗",
    "有用吗",
    "怎么样",
    "价格",
    "多少钱",
    "预算",
    "性价比",
    "敏感肌",
    "泛红",
    "刺激",
    "踩雷",
    "避雷",
    "适合",
    "平替",
)

DEFAULT_SELECTION_CONFIG = CommentSelectionConfig()


def _count(row: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return parse_cn_number(value)
    return 0


def _feature_by_note(feature_table: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("note_id", "")): item
        for item in feature_table
        if isinstance(item, dict) and str(item.get("note_id", "")).strip()
    }


def _post_score(
    content: dict[str, Any],
    feature: dict[str, Any] | None,
    config: CommentSelectionConfig,
) -> tuple[float, ...]:
    feature = feature or {}
    aliases = {
        "like_count": ("like_count", "liked_count"),
        "comment_count": ("comment_count", "comments_count"),
        "collect_count": ("collect_count", "collected_count"),
        "share_count": ("share_count",),
    }
    scores = [
        float(_count(content, *aliases.get(field, (field,))))
        for field in config.post_score_fields
    ]
    scores.append(float(feature.get("ad_fit_score", 0) or 0))
    return tuple(scores)


def _intent_score(text: str, config: CommentSelectionConfig) -> int:
    return sum(1 for keyword in config.intent_keywords if keyword in text)


def _comment_score(
    comment: dict[str, Any],
    source_index: int,
    config: CommentSelectionConfig,
) -> tuple[int, int, int]:
    text = str(comment.get("comment_text", "")).strip()
    return (
        _count(comment, *config.comment_like_fields),
        _intent_score(text, config),
        -source_index,
    )


def _selection_reason(
    comment: dict[str, Any],
    post: dict[str, Any] | None,
    config: CommentSelectionConfig,
) -> str:
    comment_likes = _count(comment, *config.comment_like_fields)
    post_likes = _count(post or {}, "like_count", "liked_count")
    if post_likes > 0 and comment_likes > 0:
        return "高赞帖子 + 高赞评论"
    if comment_likes > 0:
        return "高赞评论"
    if _intent_score(str(comment.get("comment_text", "")), config) > 0:
        return "高意图评论"
    return "帖子覆盖补位"


def select_priority_comments(
    *,
    content_table: list[dict[str, Any]],
    comment_table: list[dict[str, Any]],
    feature_table: list[dict[str, Any]] | None = None,
    limit: int = 50,
    config: CommentSelectionConfig | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """按帖子热度和评论热度选择优先进入 AI 处理/前端展示的评论。

    第一轮保证每个帖子最多取 1 条最高优先级评论，避免单个爆帖占满队列。
    第二轮再按帖子优先级循环补齐剩余名额。
    """
    runtime_config = config or DEFAULT_SELECTION_CONFIG
    runtime_config = CommentSelectionConfig(
        strategy_version=runtime_config.strategy_version,
        limit=limit,
        first_round_per_post=runtime_config.first_round_per_post,
        post_score_fields=runtime_config.post_score_fields,
        comment_like_fields=runtime_config.comment_like_fields,
        intent_keywords=runtime_config.intent_keywords,
    )
    feature_map = _feature_by_note(feature_table or [])
    content_map = {
        str(item.get("note_id", "")): item
        for item in content_table
        if isinstance(item, dict) and str(item.get("note_id", "")).strip()
    }
    post_order = sorted(
        content_map,
        key=lambda note_id: _post_score(
            content_map[note_id],
            feature_map.get(note_id),
            runtime_config,
        ),
        reverse=True,
    )

    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    seen_comments: set[str] = set()
    total_valid = 0
    for index, comment in enumerate(comment_table):
        if not isinstance(comment, dict):
            continue
        text = str(comment.get("comment_text", "")).strip()
        if not text:
            continue
        note_id = str(comment.get("note_id", "")).strip()
        comment_id = str(comment.get("comment_id") or f"{note_id}-{index}")
        dedupe_key = f"{note_id}:{comment_id}"
        if dedupe_key in seen_comments:
            continue
        seen_comments.add(dedupe_key)
        total_valid += 1
        grouped[note_id].append((index, comment))

    for note_id, comments in grouped.items():
        comments.sort(
            key=lambda item: _comment_score(item[1], item[0], runtime_config),
            reverse=True,
        )
        if note_id not in content_map:
            post_order.append(note_id)

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()

    def append_comment(note_id: str, source_index: int, comment: dict[str, Any]) -> bool:
        if len(selected) >= limit:
            return False
        comment_id = str(comment.get("comment_id") or f"{note_id}-{source_index}")
        dedupe_key = f"{note_id}:{comment_id}"
        if dedupe_key in selected_keys:
            return False
        post = content_map.get(note_id)
        enriched = dict(comment)
        enriched["comment_id"] = comment_id
        enriched["selection_rank"] = len(selected) + 1
        enriched["selection_strategy"] = runtime_config.strategy_version
        enriched["selection_reason"] = _selection_reason(comment, post, runtime_config)
        enriched["comment_like_count"] = _count(comment, *runtime_config.comment_like_fields)
        enriched["post_like_count"] = _count(post or {}, "like_count", "liked_count")
        enriched["post_comment_count"] = _count(post or {}, "comment_count", "comments_count")
        selected.append(enriched)
        selected_keys.add(dedupe_key)
        return True

    for note_id in post_order:
        comments = grouped.get(note_id, [])
        for source_index, comment in comments[: runtime_config.first_round_per_post]:
            append_comment(note_id, source_index, comment)

    cursor = {note_id: runtime_config.first_round_per_post for note_id in post_order}
    while len(selected) < limit:
        progressed = False
        for note_id in post_order:
            comments = grouped.get(note_id, [])
            idx = cursor.get(note_id, 1)
            if idx >= len(comments):
                continue
            source_index, comment = comments[idx]
            cursor[note_id] = idx + 1
            progressed = append_comment(note_id, source_index, comment) or progressed
            if len(selected) >= limit:
                break
        if not progressed:
            break

    meta = {
        "strategy_version": runtime_config.strategy_version,
        "limit": limit,
        "total_comments": len(comment_table),
        "valid_comments": total_valid,
        "selected_comments": len(selected),
        "covered_posts": len({str(item.get("note_id", "")) for item in selected}),
    }
    return selected, meta


def enrich_with_priority_comments(
    payload: dict[str, Any],
    *,
    limit: int = 50,
    config: CommentSelectionConfig | None = None,
) -> dict[str, Any]:
    """向标准化 payload 中写入优先评论表，并同步 raw_data.comments。"""
    priority_comments, meta = select_priority_comments(
        content_table=payload.get("content_table", []),
        comment_table=payload.get("comment_table", []),
        feature_table=payload.get("feature_table", []),
        limit=limit,
        config=config,
    )
    next_payload = dict(payload)
    next_payload["priority_comment_table"] = priority_comments
    next_payload["comment_selection_meta"] = meta

    raw_data = dict(next_payload.get("raw_data") or {})
    raw_data["comments"] = [
        {
            "user": str(comment.get("comment_id") or "anonymous"),
            "content": str(comment.get("comment_text", "")),
            "likes": int(comment.get("comment_like_count", comment.get("like_count", 0)) or 0),
        }
        for comment in priority_comments
    ]
    next_payload["raw_data"] = raw_data
    return next_payload
