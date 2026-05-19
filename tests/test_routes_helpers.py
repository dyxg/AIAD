import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.routes import (
    _build_progress,
    _build_review_queue,
    _build_sentiment_bars,
    _build_topic_cloud,
    _guess_sentiment,
    _load_task_payload,
)
from app.models.schemas import TaskRecord, TaskStatus
from app.services.task_store import TaskStore


def _success_record(processed_file: str) -> TaskRecord:
    now = datetime(2026, 4, 16, 12, 0, 0)
    return TaskRecord(
        task_id="task-1",
        created_at=now,
        updated_at=now,
        status=TaskStatus.SUCCESS,
        params={"platform": "xhs"},
        result={"processed_file": processed_file},
    )


def test_guess_sentiment_covers_positive_negative_and_neutral() -> None:
    assert _guess_sentiment("这个真的不错，值得推荐") == "positive"
    assert _guess_sentiment("又贵又踩雷，体验很差") == "negative"
    assert _guess_sentiment("今天只是来看看") == "neutral"


def test_build_review_queue_uses_author_and_default_comment_id() -> None:
    payload = {
        "content_table": [{"note_id": "n1", "author_name": "博主A"}],
        "comment_table": [
            {"note_id": "n1", "comment_text": "这个不错，值得推荐"},
            {"note_id": "n1", "comment_id": "c2", "comment_text": "价格太贵，有点踩雷"},
            {"note_id": "n1", "comment_text": "   "},
        ],
    }

    queue = _build_review_queue(payload, limit=2)

    assert len(queue) == 2
    assert queue[0]["comment_id"] == "n1-0"
    assert queue[0]["note_id"] == "n1"
    assert queue[0]["author"] == "博主A"
    assert queue[0]["sentiment"] == "positive"
    assert queue[0]["focus"] == "种草转化"
    assert queue[0]["likes"] == 0
    assert queue[1]["comment_id"] == "c2"
    assert queue[1]["sentiment"] == "negative"
    assert queue[1]["focus"] == "痛点回应"


def test_build_review_queue_prefers_priority_comment_table() -> None:
    payload = {
        "content_table": [
            {"note_id": "n1", "author_name": "博主A", "like_count": 1000},
            {"note_id": "n2", "author_name": "博主B", "like_count": 10},
        ],
        "comment_table": [
            {"note_id": "n2", "comment_id": "raw-first", "comment_text": "原始第一条"}
        ],
        "priority_comment_table": [
            {
                "note_id": "n1",
                "comment_id": "priority-first",
                "comment_text": "优先高赞评论",
                "comment_like_count": 88,
                "post_like_count": 1000,
                "selection_rank": 1,
                "selection_reason": "高赞帖子 + 高赞评论",
            }
        ],
    }

    queue = _build_review_queue(payload)

    assert [item["comment_id"] for item in queue] == ["priority-first"]
    assert queue[0]["note_id"] == "n1"
    assert queue[0]["likes"] == 88
    assert queue[0]["comment_like_count"] == 88
    assert queue[0]["post_like_count"] == 1000
    assert queue[0]["selection_reason"] == "高赞帖子 + 高赞评论"


def test_build_topic_cloud_filters_stopwords_and_limits_words() -> None:
    payload = {
        "feature_table": [{"topic_cluster": "beauty_care"}],
        "content_table": [
            {
                "title": "防晒 防晒 the summer look",
                "desc": "这个 防晒 场景 很适合 通勤 防晒",
            }
        ],
    }

    cloud = _build_topic_cloud(payload, limit=3)
    words = [item["word"] for item in cloud]

    assert "防晒" in words
    assert "the" not in words
    assert len(cloud) <= 3
    assert all(item["className"].startswith("text-") for item in cloud)


def test_build_sentiment_bars_sums_to_100() -> None:
    payload = {
        "comment_table": [
            {"comment_text": "不错，推荐"},
            {"comment_text": "一般般"},
            {"comment_text": "太贵了，有点差"},
        ]
    }

    bars = _build_sentiment_bars(payload)

    assert [item["label"] for item in bars] == ["POSITIVE", "NEUTRAL", "NEGATIVE"]
    assert sum(item["value"] for item in bars) == 100


def test_build_progress_uses_status_and_summary_metrics() -> None:
    record = TaskRecord(
        task_id="task-1",
        created_at=datetime(2026, 4, 16, 12, 0, 0),
        updated_at=datetime(2026, 4, 16, 12, 0, 0),
        status=TaskStatus.RUNNING,
        params={"platform": "xhs"},
    )

    progress = _build_progress(
        record,
        {"summary": {"content_count": 12, "comment_count": 34, "feature_count": 5}},
    )

    assert progress["step"]["label"] == "Content Synthesis"
    assert progress["metrics"] == {"posts_scanned": 12, "comments_read": 34}
    assert len(progress["logs"]) == 4


def test_load_task_payload_reads_processed_file(tmp_path: Path) -> None:
    store_file = tmp_path / "tasks.json"
    store_file.write_text("{}", encoding="utf-8")
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps({"summary": {"content_count": 1}}), encoding="utf-8")

    store = TaskStore(store_file)
    store.upsert(_success_record(str(payload_file)))

    record, payload = _load_task_payload("task-1", store)

    assert record.task_id == "task-1"
    assert payload["summary"]["content_count"] == 1


def test_load_task_payload_raises_when_processed_file_missing(tmp_path: Path) -> None:
    store_file = tmp_path / "tasks.json"
    store_file.write_text("{}", encoding="utf-8")
    store = TaskStore(store_file)
    store.upsert(_success_record(str(tmp_path / "missing.json")))

    with pytest.raises(HTTPException) as exc_info:
        _load_task_payload("task-1", store)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "processed file not exists"
