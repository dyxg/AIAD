from __future__ import annotations

from collections import Counter
from typing import Any


def _pick_primary_feature(feature_table: list[dict[str, Any]]) -> dict[str, Any]:
    if not feature_table:
        return {}
    return max(feature_table, key=lambda item: float(item.get("ad_fit_score", 0)))


def _flatten_media_paths(content_table: list[dict[str, Any]], limit: int = 6) -> list[str]:
    paths: list[str] = []
    for row in content_table:
        raw_paths = row.get("media_local_paths", [])
        if isinstance(raw_paths, list):
            paths.extend(str(item) for item in raw_paths if str(item).strip())
        if len(paths) >= limit:
            break
    return paths[:limit]


def _build_raw_comments(comment_table: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    for row in comment_table[:limit]:
        comments.append(
            {
                "user": str(row.get("comment_id") or "anonymous"),
                "content": str(row.get("comment_text", "")),
                "likes": int(row.get("like_count", 0) or 0),
            }
        )
    return comments


def _infer_scene(content_table: list[dict[str, Any]], feature_table: list[dict[str, Any]]) -> str:
    joined = " ".join(
        f"{row.get('title', '')} {row.get('desc', '')}" for row in content_table[:3]
    ).strip()
    if any(token in joined for token in ["海边", "沙滩", "度假"]):
        return "海边/沙滩"
    if any(token in joined for token in ["通勤", "上班", "早八"]):
        return "通勤/日常"
    primary = _pick_primary_feature(feature_table)
    topic = str(primary.get("topic_cluster", "general"))
    return topic if topic != "general" else "待补充场景"


def _infer_vibe(_content_table: list[dict[str, Any]], feature_table: list[dict[str, Any]]) -> str:
    primary = _pick_primary_feature(feature_table)
    sentiment = float(primary.get("sentiment_score", 0))
    if sentiment >= 0.3:
        return "轻松/积极/真实分享"
    if sentiment <= -0.3:
        return "谨慎/求建议/问题导向"
    return "中性/生活化"


def _detect_items(content_table: list[dict[str, Any]], limit: int = 3) -> list[str]:
    items: list[str] = []
    for row in content_table:
        tags = row.get("tags", [])
        if isinstance(tags, list):
            items.extend(str(tag).strip() for tag in tags if str(tag).strip())
    return [item for item, _ in Counter(items).most_common(limit)]


def _build_main_emotion(feature_table: list[dict[str, Any]]) -> str:
    if not feature_table:
        return "待补充评论情绪"
    avg_sentiment = sum(float(item.get("sentiment_score", 0)) for item in feature_table) / max(
        1, len(feature_table)
    )
    if avg_sentiment >= 0.3:
        return "积极，对相关产品有一定兴趣"
    if avg_sentiment <= -0.3:
        return "谨慎，对效果和风险更敏感"
    return "中性，处于比较和观望阶段"


def _collect_top_list(feature_table: list[dict[str, Any]], field: str, limit: int = 3) -> list[str]:
    values: list[str] = []
    for row in feature_table:
        raw = row.get(field, [])
        if isinstance(raw, list):
            values.extend(str(item).strip() for item in raw if str(item).strip())
        elif raw:
            values.append(str(raw).strip())
    return [item for item, _ in Counter(values).most_common(limit)]


def _build_language_style(comment_table: list[dict[str, Any]]) -> str:
    texts = " ".join(str(row.get("comment_text", "")) for row in comment_table[:20])
    if any(token in texts for token in ["哈哈", "姐妹", "宝子", "绝了"]):
        return "口语化强，带网感表达"
    if any(token in texts for token in ["?", "求", "吗"]) or "？" in texts:
        return "问答导向，带明显求助语气"
    return "生活化表达，偏真实经验分享"


def _build_rag_references(content_table: list[dict[str, Any]], comment_table: list[dict[str, Any]]) -> list[str]:
    references: list[str] = []
    for row in comment_table[:2]:
        text = str(row.get("comment_text", "")).strip()
        if text:
            references.append(text[:80])
    for row in content_table[:2]:
        text = " ".join([str(row.get("title", "")), str(row.get("desc", ""))]).strip()
        if text:
            references.append(text[:80])
    return references[:3]


def build_global_state(
    *,
    normalized: dict[str, Any],
    request_info: dict[str, Any],
) -> dict[str, Any]:
    """将当前工程数据映射为文档约定的 Global State。"""
    content_table = normalized.get("content_table", [])
    comment_table = normalized.get("priority_comment_table") or normalized.get("comment_table", [])
    feature_table = normalized.get("feature_table", [])
    top_content = content_table[0] if content_table else {}
    post_url = str(request_info.get("post_url", "") or top_content.get("note_url", ""))
    product_info = str(request_info.get("product_info", "")).strip()
    target_style = str(request_info.get("target_style", "测评风")).strip() or "测评风"
    return {
        "request_info": {
            "post_url": post_url,
            "product_info": product_info,
            "target_style": target_style,
        },
        "raw_data": {
            "post_content": " ".join(
                [str(top_content.get("title", "")), str(top_content.get("desc", ""))]
            ).strip(),
            "media_paths": _flatten_media_paths(content_table),
            "comments": _build_raw_comments(comment_table),
        },
        "vision_analysis": {
            "scene": _infer_scene(content_table, feature_table),
            "vibe": _infer_vibe(content_table, feature_table),
            "detected_items": _detect_items(content_table),
        },
        "nlp_analysis": {
            "main_emotion": _build_main_emotion(feature_table),
            "pain_points": _collect_top_list(feature_table, "pain_points"),
            "language_style": _build_language_style(comment_table),
        },
        "rag_references": _build_rag_references(content_table, comment_table),
        "final_ads": [],
        "review_score": 0,
    }
