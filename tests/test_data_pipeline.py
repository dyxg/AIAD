import json
from pathlib import Path

import httpx

from app.core.config import Settings
from app.services.chroma_store import ChromaStore
from app.services.comment_selector import enrich_with_priority_comments
from app.services.llm_gateway import OpenAICompatibleGateway
from app.services.normalize import normalize_dataset
from app.services.state_builder import build_global_state
from app.workflows.data_graph import run_data_workflow


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    """写入测试用 JSONL 文件。"""
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_data_pipeline_normalize_and_graph(tmp_path: Path) -> None:
    """验证标准化与 LangGraph 整理链路可正常输出。"""
    content_file = tmp_path / "search_contents_demo.jsonl"
    comment_file = tmp_path / "search_comments_demo.jsonl"
    media_root_dir = tmp_path / "media_root"
    image_dir = media_root_dir / "images" / "n1"
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "beach_hat.jpg").write_bytes(b"fake-image")

    _write_jsonl(
        content_file,
        [
            {
                "note_id": "n1",
                "title": "敏感肌修护精华",
                "desc": "成分党推荐，预算友好",
                "liked_count": "1.2万",
                "comments_count": "356",
                "collected_count": "999",
                "share_count": "33",
                "nickname": "作者A",
                "url": "https://xhs.example/n1",
                "time": "2025-10-01",
            }
        ],
    )
    _write_jsonl(
        comment_file,
        [
            {
                "comment_id": "c1",
                "note_id": "n1",
                "content": "学生党觉得价格可以，效果也不错",
                "create_time": "2025-10-01 10:00:00",
                "like_count": "88",
                "ip_location": "上海",
            }
        ],
    )

    normalized = normalize_dataset(
        platform="xhs",
        source_keyword="修护,敏感肌",
        content_file=content_file,
        comment_file=comment_file,
        media_root_dir=media_root_dir,
        product_info="敏感肌修护精华",
    )
    normalized = enrich_with_priority_comments(normalized)
    settings = Settings(
        project_root=tmp_path,
        aiad_python_exe=Path("/usr/bin/python3"),
        media_crawler_dir=tmp_path / "vendor" / "MediaCrawler",
        crawler_output_dir=tmp_path / "data" / "raw",
        processed_output_dir=tmp_path / "data" / "processed",
        logs_dir=tmp_path / "logs",
        task_store_file=tmp_path / "data" / "tasks.json",
        chroma_persist_dir=tmp_path / "data" / "chroma",
        mediacrawler_python_exe=Path("/usr/bin/python3"),
        playwright_browsers_path=tmp_path / ".ms-playwright",
        vision_provider="mock",
        vision_model="mock-vision",
    )
    request_info = {
        "post_url": "https://www.xiaohongshu.com/explore/n1",
        "product_info": "修护精华，特点：温和维稳，适合敏感肌",
        "target_style": "测评风",
    }

    output = run_data_workflow(normalized, request_info=request_info, settings=settings)

    assert output["summary"]["content_count"] == 1
    assert output["content_table"][0]["like_count"] == 12000
    assert output["comment_table"][0]["comment_id"] == "c1"
    assert output["priority_comment_table"][0]["comment_id"] == "c1"
    assert output["comment_selection_meta"]["selected_comments"] == 1
    assert "feature_table" in output
    assert output["vision_analysis"]["source_media_count"] == 1
    assert output["vision_analysis"]["model_provider"] == "mock"
    assert output["request_info"]["product_info"].startswith("修护精华")
    assert output["global_state"]["request_info"]["target_style"] == "测评风"
    assert output["global_state"]["vision_analysis"]["scene"] == "海边/沙滩"
    assert output["global_state"]["nlp_analysis"]["pain_points"]
    assert output["prompt_bundle"]["prompt_version"] == "agent5-v4"
    assert "system_prompt" in output["prompt_bundle"]
    assert output["llm_result"]["status"] == "not_configured"
    assert output["final_ads"] == []


def test_build_global_state_matches_doc_shape(tmp_path: Path) -> None:
    normalized = {
        "summary": {"platform": "xhs", "content_count": 1, "comment_count": 1, "feature_count": 1},
        "content_table": [
            {
                "note_id": "n1",
                "title": "今天去海边玩啦",
                "desc": "太阳好大，想找个温和点的防晒。",
                "note_url": "https://www.xiaohongshu.com/explore/n1",
                "tags": ["草帽", "墨镜"],
                "media_local_paths": [str(tmp_path / "1.jpg")],
            }
        ],
        "comment_table": [
            {"comment_id": "c1", "comment_text": "求博主的防晒！", "like_count": 120}
        ],
        "priority_comment_table": [
            {"comment_id": "c1", "comment_text": "求博主的防晒！", "like_count": 120}
        ],
        "feature_table": [
            {
                "ad_fit_score": 1.3,
                "topic_cluster": "beauty_care",
                "sentiment_score": 0.6,
                "pain_points": ["怕晒黑", "需要海边适用的高倍防晒"],
            }
        ],
    }

    state = build_global_state(
        normalized=normalized,
        request_info={
            "post_url": "https://www.xiaohongshu.com/explore/n1",
            "product_info": "蕉下防晒霜，特点：水润不假白，适合敏感肌",
            "target_style": "测评风",
        },
    )

    assert set(state.keys()) == {
        "request_info",
        "raw_data",
        "vision_analysis",
        "nlp_analysis",
        "rag_references",
        "final_ads",
        "review_score",
    }
    assert state["request_info"]["product_info"].startswith("蕉下防晒霜")
    assert state["raw_data"]["comments"][0]["content"] == "求博主的防晒！"
    assert state["vision_analysis"]["detected_items"] == ["草帽", "墨镜"]
    assert state["nlp_analysis"]["pain_points"][0] == "怕晒黑"
    assert state["final_ads"] == []


def test_data_workflow_with_mock_llm_gateway() -> None:
    normalized = {
        "summary": {"platform": "xhs", "content_count": 1, "comment_count": 1, "feature_count": 1},
        "content_table": [
            {
                "note_id": "n1",
                "title": "今天去海边玩啦",
                "desc": "太阳好大，想找个温和点的防晒。",
                "note_url": "https://www.xiaohongshu.com/explore/n1",
            }
        ],
        "comment_table": [
            {"comment_id": "c1", "comment_text": "求博主的防晒！", "like_count": 120}
        ],
        "feature_table": [
            {
                "ad_fit_score": 1.3,
                "topic_cluster": "beauty_care",
                "sentiment_score": 0.6,
                "pain_points": ["怕晒黑"],
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '[{"style":"测评风","content":"海边这种场景我会更看重防晒的肤感和稳定性。"}]'
                        }
                    }
                ]
            },
        )

    gateway = OpenAICompatibleGateway(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen2.5:3b-instruct",
        api_key="local-dev",
        timeout_seconds=30,
        temperature=0.7,
        max_tokens=1200,
        provider="local",
        transport=httpx.MockTransport(handler),
    )

    output = run_data_workflow(
        normalized,
        request_info={
            "post_url": "https://www.xiaohongshu.com/explore/n1",
            "product_info": "蕉下防晒霜，特点：水润不假白，适合敏感肌",
            "target_style": "测评风",
        },
        llm_gateway=gateway,
    )

    assert output["llm_result"]["status"] == "success"
    assert output["final_ads"][0]["style"] == "测评风"
    assert output["global_state"]["final_ads"][0]["content"].startswith("海边这种场景")


def test_data_pipeline_chromadb_persist(tmp_path: Path) -> None:
    """验证整理结果可写入 ChromaDB。"""
    payload = {
        "summary": {"platform": "xhs", "content_count": 1, "comment_count": 1, "feature_count": 1},
        "content_table": [{"platform": "xhs", "note_id": "n1", "title": "t"}],
        "comment_table": [{"platform": "xhs", "note_id": "n1", "comment_id": "c1"}],
        "feature_table": [{"platform": "xhs", "note_id": "n1", "topic_cluster": "general"}],
    }
    store = ChromaStore(tmp_path / "chroma")
    counts = store.write_task_payload("task1", payload)
    assert counts["summary_count"] == 1
    assert counts["content_count"] == 1
    assert counts["comment_count"] == 1
    assert counts["feature_count"] == 1
