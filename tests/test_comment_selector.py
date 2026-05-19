from app.services.comment_selector import (
    CommentSelectionConfig,
    enrich_with_priority_comments,
    select_priority_comments,
)


def test_select_priority_comments_prefers_hot_posts_and_one_comment_per_post_first() -> None:
    content_table = [
        {"note_id": "cold", "like_count": 10, "comment_count": 5},
        {"note_id": "hot", "like_count": 1000, "comment_count": 200},
        {"note_id": "warm", "like_count": 500, "comment_count": 50},
    ]
    comment_table = [
        {"note_id": "cold", "comment_id": "cold-1", "comment_text": "普通评论", "like_count": 1},
        {
            "note_id": "hot",
            "comment_id": "hot-1",
            "comment_text": "求链接，好用吗",
            "like_count": 20,
        },
        {"note_id": "hot", "comment_id": "hot-2", "comment_text": "第二条高赞", "like_count": 999},
        {
            "note_id": "warm",
            "comment_id": "warm-1",
            "comment_text": "敏感肌适合吗",
            "like_count": 10,
        },
    ]

    selected, meta = select_priority_comments(
        content_table=content_table,
        comment_table=comment_table,
        limit=3,
    )

    assert [item["comment_id"] for item in selected] == ["hot-2", "warm-1", "cold-1"]
    assert meta["selected_comments"] == 3
    assert meta["covered_posts"] == 3


def test_select_priority_comments_backfills_second_comment_after_post_coverage() -> None:
    content_table = [
        {"note_id": "n1", "like_count": 100},
        {"note_id": "n2", "like_count": 50},
    ]
    comment_table = [
        {"note_id": "n1", "comment_id": "c1", "comment_text": "第一条", "like_count": 1},
        {"note_id": "n1", "comment_id": "c2", "comment_text": "第二条", "like_count": 99},
        {"note_id": "n2", "comment_id": "c3", "comment_text": "第三条", "like_count": 2},
    ]

    selected, _ = select_priority_comments(
        content_table=content_table,
        comment_table=comment_table,
        limit=3,
    )

    assert [item["comment_id"] for item in selected] == ["c2", "c3", "c1"]


def test_select_priority_comments_accepts_legacy_likes_field() -> None:
    selected, _ = select_priority_comments(
        content_table=[{"note_id": "n1", "like_count": 1}],
        comment_table=[
            {"note_id": "n1", "comment_id": "low", "comment_text": "低赞", "likes": 1},
            {"note_id": "n1", "comment_id": "high", "comment_text": "高赞", "likes": "1.2万"},
        ],
        limit=1,
    )

    assert selected[0]["comment_id"] == "high"
    assert selected[0]["comment_like_count"] == 12000


def test_select_priority_comments_allows_strategy_config_override() -> None:
    selected, meta = select_priority_comments(
        content_table=[{"note_id": "n1", "like_count": 1}],
        comment_table=[
            {"note_id": "n1", "comment_id": "plain", "comment_text": "普通", "like_count": 0},
            {
                "note_id": "n1",
                "comment_id": "custom",
                "comment_text": "自定义强意图",
                "like_count": 0,
            },
        ],
        limit=1,
        config=CommentSelectionConfig(
            strategy_version="custom-v1",
            intent_keywords=("自定义强意图",),
        ),
    )

    assert selected[0]["comment_id"] == "custom"
    assert selected[0]["selection_strategy"] == "custom-v1"
    assert meta["strategy_version"] == "custom-v1"


def test_enrich_with_priority_comments_updates_raw_data_comments() -> None:
    payload = {
        "raw_data": {"comments": [{"content": "旧评论", "likes": 0}]},
        "content_table": [{"note_id": "n1", "like_count": 100}],
        "comment_table": [
            {"note_id": "n1", "comment_id": "c1", "comment_text": "求推荐", "like_count": 8}
        ],
        "feature_table": [],
    }

    enriched = enrich_with_priority_comments(payload, limit=50)

    assert enriched["priority_comment_table"][0]["comment_id"] == "c1"
    assert enriched["raw_data"]["comments"] == [
        {"user": "c1", "content": "求推荐", "likes": 8}
    ]
    assert enriched["comment_selection_meta"]["strategy_version"] == "priority-comments-v1"
