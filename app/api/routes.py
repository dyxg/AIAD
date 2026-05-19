from __future__ import annotations

import asyncio
import json
import logging
import queue as stdlib_queue
import re
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.config import Settings, get_settings
from app.models.schemas import (
    AdDraft,
    AnalyzeOutput,
    ContextRunRequest,
    CopywriterRunRequest,
    ErrorCode,
    GlobalAgentState,
    NLPAnalysis,
    RagRunRequest,
    RunRequest,
    TaskRecord,
    TaskResponse,
    TaskStatus,
    VisionAnalysis,
    VisionRunRequest,
)
from app.services.chroma_store import ChromaStore
from app.services.comment_selector import enrich_with_priority_comments
from app.services.context_agent import ContextAgent
from app.services.copywriter import build_per_comment_prompt, parse_per_comment_result
from app.services.copywriter_agent import CopywriterAgent
from app.services.crawler_runner import run_crawler
from app.services.keyword_cache import CacheHit, KeywordCache
from app.services.llm_gateway import build_llm_gateway
from app.services.normalize import normalize_dataset
from app.services.rag_agent import RagAgent
from app.services.task_store import TaskStore
from app.services.vision import VisionAgent
from app.workflows.data_graph import run_data_workflow

router = APIRouter(prefix="/api/ad-intel", tags=["ad-intel"])
logger = logging.getLogger(__name__)


def _task_store(settings: Settings = Depends(get_settings)) -> TaskStore:
    return TaskStore(settings.task_store_file)


def _build_state_template() -> GlobalAgentState:
    return GlobalAgentState()


_PER_COMMENT_BATCH_SIZE = 8  # 每批 tokens 约 400-500，耗时 <30s


def _run_per_comment_background(task_id: str, processed_path: Path, settings: Settings) -> None:
    """后台守护线程：对全量评论分批生成植入文案，每批完成后立即写回 processed JSON 文件。

    前端轮询 /insights 端点，每次读取最新文件，实现渐进式展示。
    """
    task_store = TaskStore(settings.task_store_file)
    llm_gateway = build_llm_gateway(settings)
    if llm_gateway is None:
        task_store.patch_result(task_id, {
            "comment_ads_status": "complete",
            "comment_ads_done": 0,
            "comment_ads_total": 0,
        })
        return

    try:
        current_payload: dict[str, Any] = json.loads(processed_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("逐评论后台任务读文件失败 task_id=%s error=%s", task_id, exc)
        task_store.patch_result(task_id, {"comment_ads_status": "error"})
        return

    comment_table: list[dict[str, Any]] = current_payload.get("comment_table", [])
    request_info: dict[str, Any] = current_payload.get("request_info", {})
    nlp_analysis: dict[str, Any] = current_payload.get("nlp_analysis", {})
    pain_points: list[str] = list(nlp_analysis.get("pain_points", []))
    product_info = str(request_info.get("product_info", ""))
    target_style = str(request_info.get("target_style", "测评风"))

    valid_comments = [
        c
        for c in current_payload.get("priority_comment_table", comment_table)
        if isinstance(c, dict) and str(c.get("comment_text", "")).strip()
    ]
    total = len(valid_comments)
    batches = [
        valid_comments[i: i + _PER_COMMENT_BATCH_SIZE]
        for i in range(0, total, _PER_COMMENT_BATCH_SIZE)
    ]
    total_batches = len(batches)
    done_count = 0

    task_store.patch_result(task_id, {
        "comment_ads_status": "processing",
        "comment_ads_done": 0,
        "comment_ads_total": total,
    })
    logger.info(
        "逐评论后台任务开始 task_id=%s total=%d batches=%d",
        task_id, total, total_batches,
    )

    for batch_idx, batch in enumerate(batches):
        try:
            fresh: dict[str, Any] = json.loads(processed_path.read_text(encoding="utf-8"))
            comment_ads: dict[str, str] = fresh.get("comment_ads") or {}
        except Exception as exc:
            logger.error(
                "逐评论后台读文件失败 task_id=%s batch=%d error=%s",
                task_id,
                batch_idx,
                exc,
            )
            break

        try:
            prompt_bundle = build_per_comment_prompt(
                comments=batch,
                product_info=product_info,
                pain_points=pain_points,
                target_style=target_style,
            )
            result = llm_gateway.generate(prompt_bundle)
            llm_status = result.get("status", "unknown")
            if llm_status == "error":
                logger.error(
                    "逐评论后台批次 %d/%d 失败 message=%s",
                    batch_idx + 1, total_batches, result.get("message", ""),
                )
            elif llm_status == "parse_error":
                logger.warning(
                    "逐评论后台批次 %d/%d 响应异常，跳过",
                    batch_idx + 1, total_batches,
                )
            else:
                batch_ads = parse_per_comment_result(result.get("raw_response", ""))
                comment_ads.update(batch_ads)
                done_count += len(batch)
                logger.info(
                    "逐评论后台批次 %d/%d 完成，本批 %d 条，累计已处理 %d/%d 条",
                    batch_idx + 1, total_batches, len(batch_ads), done_count, total,
                )
        except Exception as exc:
            logger.error(
                "逐评论后台批次 %d/%d 异常 error=%s",
                batch_idx + 1, total_batches, str(exc), exc_info=True,
            )

        try:
            fresh["comment_ads"] = comment_ads
            processed_path.write_text(
                json.dumps(fresh, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error("逐评论后台写文件失败 task_id=%s error=%s", task_id, exc)

        task_store.patch_result(task_id, {
            "comment_ads_done": done_count,
            "comment_ads_total": total,
        })

    task_store.patch_result(task_id, {
        "comment_ads_status": "complete",
        "comment_ads_done": done_count,
        "comment_ads_total": total,
    })
    logger.info("逐评论后台任务完成 task_id=%s done=%d total=%d", task_id, done_count, total)


def _run_pipeline_task(
    task_id: str,
    payload: RunRequest,
    settings: Settings,
    app_state: Any,
) -> None:
    """
    后台管道任务。

    并发安全设计：
    - keyword_cache：来自 app.state 的全局单例（唯一 Chroma 客户端 + embedding 模型）
    - inflight_keywords：in-flight 去重，同关键词并发请求只爬一次，后来者等待复用缓存
    - TaskStore.upsert()：类级别锁，防止 JSON 文件并发写冲突
    - keyword_cache.store()：实例级别锁，防止并发 upsert 冲突
    SSE 流式推送：各阶段完成后通过 emit() 写入 task_event_queues[task_id]，
    由 stream_task_events 端点消费并推送到前端。
    """
    task_store = TaskStore(settings.task_store_file)
    keyword_cache: KeywordCache = app_state.keyword_cache
    inflight_keywords: dict[str, threading.Event] = app_state.inflight_keywords
    inflight_lock: threading.Lock = app_state.inflight_lock

    # SSE 事件队列（由 run_analysis 预先创建，run_analysis 结束后此队列仍在 app.state 中）
    event_q: stdlib_queue.Queue | None = getattr(app_state, "task_event_queues", {}).get(task_id)

    def emit(event: dict[str, Any]) -> None:
        if event_q is not None:
            try:
                event_q.put_nowait(event)
            except Exception:
                pass

    keywords = payload.keywords or [payload.ad_type]
    primary_keyword = keywords[0]
    inflight_key = f"{payload.platform}:{primary_keyword}"

    cache_hit: CacheHit | None = None
    content_file: Path | None = None
    comment_file: Path | None = None
    media_root_dir: Path | None = None

    emit({"type": "progress", "stage": "init", "percent": 5, "message": "任务初始化中..."})

    try:
        # === 1. 语义缓存查询 ===
        cache_hit = keyword_cache.lookup(
            primary_keyword,
            payload.platform,
            ttl_hours=settings.keyword_cache_ttl_hours,
        )

        if cache_hit is not None:
            logger.info(
                "缓存命中，跳过爬虫 task_id=%s keyword=%s distance=%.4f",
                task_id, cache_hit.keyword, cache_hit.distance,
            )
            content_file = Path(cache_hit.content_file)
            comment_file = Path(cache_hit.comment_file) if cache_hit.comment_file else None
            emit({"type": "progress", "stage": "cache_hit", "percent": 30,
                  "message": f"命中语义缓存，跳过爬取（关键词：{cache_hit.keyword}）"})
        else:
            # === 2. in-flight 并发去重 ===
            # 同一关键词若已有其他线程在爬取，等待其完成后复用缓存，不重复爬
            is_inflight_owner = False
            inflight_event: threading.Event | None = None

            with inflight_lock:
                existing = inflight_keywords.get(inflight_key)
                if existing is not None:
                    inflight_event = existing
                else:
                    inflight_event = threading.Event()
                    inflight_keywords[inflight_key] = inflight_event
                    is_inflight_owner = True

            if not is_inflight_owner and inflight_event is not None:
                emit({
                    "type": "progress",
                    "stage": "waiting",
                    "percent": 10,
                    "message": "同关键词正在抓取中，等待复用...",
                })
                logger.info(
                    "关键词 in-flight，等待复用缓存 keyword=%s task_id=%s",
                    primary_keyword, task_id,
                )
                inflight_event.wait(timeout=180)
                # owner 爬完并写缓存后，重新查询
                cache_hit = keyword_cache.lookup(
                    primary_keyword,
                    payload.platform,
                    ttl_hours=settings.keyword_cache_ttl_hours,
                )
                if cache_hit is not None:
                    content_file = Path(cache_hit.content_file)
                    comment_file = Path(cache_hit.comment_file) if cache_hit.comment_file else None
                    emit({"type": "progress", "stage": "cache_hit", "percent": 30,
                          "message": "等待完成，命中共享缓存"})
                else:
                    # owner 爬取失败，降级自行爬（不再注册 inflight，避免链式阻塞）
                    logger.warning(
                        "in-flight 等待后缓存仍为空，降级自行爬取 task_id=%s", task_id
                    )

            # === 3. 执行爬取（owner 线程 或 降级路径）===
            if content_file is None:
                try:
                    if is_inflight_owner:
                        emit({"type": "progress", "stage": "crawling", "percent": 10,
                              "message": f"正在抓取「{primary_keyword}」的相关内容..."})
                    crawler = run_crawler(
                        settings=settings,
                        task_store=task_store,
                        platform=payload.platform,
                        keywords=keywords,
                        limit=payload.limit,
                        max_comments_per_note=payload.max_comments_per_note,
                        enable_media_download=payload.enable_media_download,
                        task_id=task_id,
                        emit_fn=emit,
                    )
                    if crawler.status == TaskStatus.FAILED:
                        record = task_store.get(task_id)
                        if record:
                            record.status = TaskStatus.FAILED
                            record.error_code = ErrorCode.CRAWLER_ERROR
                            record.error_message = "爬虫返回失败状态"
                            task_store.upsert(record)
                        emit({"type": "error", "message": "爬虫返回失败状态"})
                        return

                    cf = Path(crawler.output_files.get("content_file", ""))
                    if not cf.exists():
                        record = task_store.get(task_id)
                        if record:
                            record.status = TaskStatus.FAILED
                            record.error_code = ErrorCode.CRAWLER_ERROR
                            record.error_message = "未找到内容数据文件"
                            task_store.upsert(record)
                        emit({"type": "error", "message": "未找到内容数据文件"})
                        return

                    comment_file_raw = crawler.output_files.get("comment_file")
                    content_file = cf
                    comment_file = Path(comment_file_raw) if comment_file_raw else None
                    if crawler.output_files.get("media_root_dir"):
                        media_root_dir = Path(crawler.output_files["media_root_dir"])

                    # 爬取成功 → 立即写缓存，再释放 inflight event
                    # 等待者醒来后能查到缓存，不必重复爬
                    raw_line_count = 0
                    try:
                        raw_line_count = sum(
                            1 for line in content_file.read_text(encoding="utf-8").splitlines()
                            if line.strip()
                        )
                    except Exception:
                        pass
                    keyword_cache.store(
                        task_id=task_id,
                        keyword=primary_keyword,
                        platform=payload.platform,
                        content_file=str(content_file),
                        comment_file=str(comment_file) if comment_file else "",
                        result_count=raw_line_count,
                    )
                    emit({"type": "progress", "stage": "crawled", "percent": 30,
                          "message": f"数据抓取完成，共 {raw_line_count} 条原始内容"})

                finally:
                    # 无论爬取成功或失败，必须释放 inflight，防止等待者永久阻塞
                    if is_inflight_owner and inflight_event is not None:
                        with inflight_lock:
                            inflight_keywords.pop(inflight_key, None)
                        inflight_event.set()

        if content_file is None:
            logger.error("content_file 为空，流程终止 task_id=%s", task_id)
            emit({"type": "error", "message": "内容文件为空，流程终止"})
            return

        # === 4. 数据标准化 ===
        emit({
            "type": "progress",
            "stage": "normalizing",
            "percent": 35,
            "message": "正在标准化数据...",
        })
        normalized = normalize_dataset(
            platform=payload.platform,
            source_keyword=",".join(keywords),
            content_file=content_file,
            comment_file=comment_file,
            media_root_dir=media_root_dir,
            product_info=payload.ad_type,
        )
        normalized = enrich_with_priority_comments(normalized)

        # 推送爬取摘要指标
        summary_data = normalized.get("summary", {})
        emit({
            "type": "crawl_done",
            "content_count": int(summary_data.get("content_count", 0)),
            "comment_count": int(summary_data.get("comment_count", 0)),
        })

        # 逐条推送评论，前端可实时展示
        _emit_comments(normalized, emit)

        # === 5. LangGraph 工作流 ===
        emit({
            "type": "progress",
            "stage": "analyzing",
            "percent": 45,
            "message": "AI 智能分析中...",
        })
        request_info = {
            "post_url": payload.post_url,
            "product_info": payload.product_info or payload.ad_type,
            "target_style": payload.target_style,
        }
        final_payload = run_data_workflow(
            normalized,
            request_info=request_info,
            settings=settings,
            llm_gateway=build_llm_gateway(settings),
            emit_fn=emit,
        )
        chroma_counts = ChromaStore(settings.chroma_persist_dir).write_task_payload(
            task_id,
            final_payload,
        )
        processed_path = settings.processed_output_dir / f"{task_id}_normalized.json"
        processed_path.write_text(
            json.dumps(final_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        record = task_store.get(task_id)
        if record:
            record.status = TaskStatus.SUCCESS
            record.result = {
                "processed_file": str(processed_path),
                "chroma_counts": chroma_counts,
                "vision_analysis": final_payload.get("vision_analysis", {}),
                "cache_hit": cache_hit is not None,
                "cache_source_task_id": cache_hit.task_id if cache_hit is not None else None,
                "comment_ads_status": "pending",
                "comment_ads_done": 0,
                "comment_ads_total": 0,
            }
            record.error_code = None
            record.error_message = ""
            task_store.upsert(record)

        logger.info("数据整理完成 task_id=%s output=%s", task_id, processed_path)
        emit({"type": "done", "task_id": task_id})

        # 启动逐评论后台线程，处理全量评论（不阻塞主流程）
        bg_thread = threading.Thread(
            target=_run_per_comment_background,
            args=(task_id, processed_path, settings),
            daemon=True,
            name=f"per_comment_{task_id}",
        )
        bg_thread.start()
        logger.info("逐评论后台线程已启动 task_id=%s thread=%s", task_id, bg_thread.name)

        # 为了在终端直观看到大模型生成的文案，打印最终结果
        final_ads = final_payload.get("final_ads", [])
        if final_ads:
            logger.info("任务 %s 生成文案 %d 条", task_id, len(final_ads))

    except Exception as exc:
        logger.error("任务执行异常 task_id=%s error=%s", task_id, str(exc))
        record = task_store.get(task_id)
        if record:
            record.status = TaskStatus.FAILED
            record.error_code = ErrorCode.INTERNAL_ERROR
            record.error_message = str(exc)[:1000]
            task_store.upsert(record)
        emit({"type": "error", "message": str(exc)[:200]})


def _load_task_payload(task_id: str, task_store: TaskStore) -> tuple[TaskRecord, dict[str, Any]]:
    record = task_store.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="task not found")
    if record.status != TaskStatus.SUCCESS:
        raise HTTPException(status_code=409, detail="task not completed")
    processed_file = record.result.get("processed_file")
    if not processed_file:
        raise HTTPException(status_code=404, detail="processed file not found")
    result_file = Path(processed_file)
    if not result_file.exists():
        raise HTTPException(status_code=404, detail="processed file not exists")
    return record, json.loads(result_file.read_text(encoding="utf-8"))


def _guess_sentiment(text: str) -> str:
    positive_words = (
        "喜欢",
        "好",
        "推荐",
        "不错",
        "满意",
        "划算",
        "值得",
        "棒",
        "love",
        "great",
        "good",
    )
    negative_words = (
        "差",
        "不好",
        "贵",
        "坑",
        "失望",
        "一般",
        "垃圾",
        "踩雷",
        "bad",
        "expensive",
        "worse",
    )
    lower_text = text.lower()
    pos_score = sum(word in lower_text for word in positive_words)
    neg_score = sum(word in lower_text for word in negative_words)
    if pos_score > neg_score:
        return "positive"
    if neg_score > pos_score:
        return "negative"
    return "neutral"


def _build_single_review_item(
    comment: dict[str, Any],
    content_map: dict[str, Any],
    index: int,
    comment_ads: dict[str, str] | None = None,
) -> dict[str, Any]:
    """将单条标准化评论转换为审核队列项（含启发式情感分析）。"""
    text = str(comment.get("comment_text", "")).strip()
    sentiment = _guess_sentiment(text)
    affinity_map = {"positive": 92, "neutral": 76, "negative": 58}
    focus_map = {"positive": "种草转化", "neutral": "品牌曝光", "negative": "痛点回应"}
    note_id = comment.get("note_id", "")
    source_meta = content_map.get(note_id, {})
    comment_like_count = int(
        comment.get("comment_like_count", comment.get("like_count", comment.get("likes", 0))) or 0
    )

    ad_text: str
    if comment_ads is None:
        # SSE 实时流阶段：per_comment_copy 节点尚未运行
        ad_text = "AI 文案生成中，请稍候..."
    elif comment_ads:
        resolved_id = str(comment.get("comment_id") or f"{note_id}-{index}")
        ad_text = comment_ads.get(resolved_id, "AI 文案未覆盖此条评论")
    else:
        # comment_ads = {} 空字典：LLM 调用失败（如超时），流水线已完成但未生成文案
        ad_text = "AI 文案生成失败，请重新发起任务"

    return {
        "comment_id": comment.get("comment_id") or f"{note_id}-{index}",
        "note_id": str(note_id),
        "author": source_meta.get("author_name", "匿名用户"),
        "platform": "小红书",
        "source_text": text[:220],
        "ad_text": ad_text,
        "predicted_affinity": affinity_map.get(sentiment, 70),
        "focus": focus_map.get(sentiment, "品牌曝光"),
        "sentiment": sentiment,
        "likes": comment_like_count,
        "comment_like_count": comment_like_count,
        "post_like_count": int(
            comment.get("post_like_count", source_meta.get("like_count", 0)) or 0
        ),
        "selection_rank": int(comment.get("selection_rank", index + 1) or index + 1),
        "selection_reason": comment.get("selection_reason", "优先评论"),
    }


def _build_review_queue(payload: dict[str, Any], limit: int = 50) -> list[dict[str, Any]]:
    content_map = {
        item.get("note_id", ""): item
        for item in payload.get("content_table", [])
        if isinstance(item, dict)
    }
    # 取 per_comment_copy 节点生成的逐评论文案映射 {comment_id: ad_text}
    # None 表示 SSE 实时阶段（管道未完成）；{} 表示 LLM 调用失败；非空 dict 表示正常结果
    raw_ads = payload.get("comment_ads")
    comment_ads: dict[str, str] | None = raw_ads if isinstance(raw_ads, dict) else None
    comment_source = payload.get("priority_comment_table") or payload.get("comment_table", [])
    items: list[dict[str, Any]] = []
    for index, comment in enumerate(comment_source):
        if not isinstance(comment, dict):
            continue
        text = str(comment.get("comment_text", "")).strip()
        if not text:
            continue
        items.append(_build_single_review_item(comment, content_map, index, comment_ads))
        if len(items) >= limit:
            break
    return items


def _emit_comments(normalized: dict[str, Any], emit: Any) -> None:
    """normalize 后逐条 emit 优先评论事件，让前端实时看到评论流入。"""
    comment_table = normalized.get("priority_comment_table") or normalized.get("comment_table", [])
    content_map = {
        item.get("note_id", ""): item
        for item in normalized.get("content_table", [])
        if isinstance(item, dict)
    }
    count = 0
    for i, comment in enumerate(comment_table):
        if not isinstance(comment, dict):
            continue
        text = str(comment.get("comment_text", "")).strip()
        if not text:
            continue
        emit({"type": "comment", "data": _build_single_review_item(comment, content_map, i)})
        count += 1
        if count >= 50:
            break


def _build_topic_cloud(payload: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    source_text = []
    for row in payload.get("feature_table", []):
        if isinstance(row, dict) and row.get("topic_cluster"):
            source_text.append(str(row.get("topic_cluster", "")))
    for row in payload.get("content_table", [])[:200]:
        if isinstance(row, dict):
            source_text.append(str(row.get("title", "")))
            source_text.append(str(row.get("desc", "")))
    tokens = re.findall(r"[\u4e00-\u9fa5]{2,}|[A-Za-z]{3,}", " ".join(source_text))
    stopwords = {
        "我们",
        "这个",
        "那个",
        "就是",
        "the",
        "and",
        "for",
        "with",
        "that",
        "from",
        "this",
    }
    filtered = [token for token in tokens if token.lower() not in stopwords]
    counter = Counter(filtered)
    classes = [
        "text-5xl font-black text-primary-container",
        "text-4xl font-extrabold text-primary",
        "text-3xl font-bold text-secondary",
        "text-2xl font-semibold text-tertiary-container",
        "text-xl font-medium text-on-surface-variant",
    ]
    cloud = []
    for index, item in enumerate(counter.most_common(limit)):
        cloud.append({"word": item[0], "className": classes[index % len(classes)]})
    return cloud


def _build_sentiment_bars(payload: dict[str, Any]) -> list[dict[str, Any]]:
    counts = {"positive": 0, "neutral": 0, "negative": 0}
    for row in payload.get("comment_table", []):
        if not isinstance(row, dict):
            continue
        sentiment = _guess_sentiment(str(row.get("comment_text", "")))
        counts[sentiment] += 1
    total = max(1, sum(counts.values()))
    positive = round(counts["positive"] * 100 / total)
    neutral = round(counts["neutral"] * 100 / total)
    negative = max(0, 100 - positive - neutral)
    return [
        {"label": "POSITIVE", "value": positive, "colorClass": "bg-secondary"},
        {"label": "NEUTRAL", "value": neutral, "colorClass": "bg-tertiary-container"},
        {"label": "NEGATIVE", "value": negative, "colorClass": "bg-error"},
    ]


def _build_progress(record: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
    if record.status == TaskStatus.SUCCESS:
        step = {"current": 4, "total": 4, "label": "Final Report", "percent": 100}
    elif record.status == TaskStatus.RUNNING:
        step = {"current": 3, "total": 4, "label": "Content Synthesis", "percent": 68}
    else:
        step = {"current": 4, "total": 4, "label": "Failed", "percent": 100}
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    now = datetime.now().strftime("%H:%M:%S")
    return {
        "step": step,
        "metrics": {
            "posts_scanned": int(summary.get("content_count", 0)),
            "comments_read": int(summary.get("comment_count", 0)),
        },
        "logs": [
            f"{now} [SYSTEM] 任务状态：{record.status.value}",
            f"{now} [CRAWLER] 内容条数：{summary.get('content_count', 0)}",
            f"{now} [ANALYSIS] 评论条数：{summary.get('comment_count', 0)}",
            f"{now} [FEATURE] 特征条数：{summary.get('feature_count', 0)}",
        ],
    }


@router.post("/run", response_model=TaskResponse)
def run_analysis(
    request: Request,
    payload: RunRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    task_store: TaskStore = Depends(_task_store),
) -> TaskResponse:
    """执行广告分析任务。"""
    if not payload.ad_type.strip():
        raise HTTPException(
            status_code=422,
            detail={"error_code": ErrorCode.INVALID_INPUT.value, "message": "ad_type不能为空"},
        )
    task_id = uuid4().hex[:12]

    # 提前创建 SSE 事件队列，确保后台任务启动时队列已就绪
    event_q: stdlib_queue.Queue = stdlib_queue.Queue()
    request.app.state.task_event_queues[task_id] = event_q

    record = TaskRecord(
        task_id=task_id,
        status=TaskStatus.RUNNING,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        params={
            "ad_type": payload.ad_type,
            "post_url": payload.post_url,
            "product_info": payload.product_info,
            "target_style": payload.target_style,
            "keywords": payload.keywords,
            "platform": payload.platform,
            "limit": payload.limit,
            "max_comments_per_note": payload.max_comments_per_note,
            "enable_media_download": payload.enable_media_download,
            "time_range": payload.time_range,
        },
    )
    task_store.upsert(record)
    # 传入 app.state，后台任务通过它访问全局单例（keyword_cache、inflight 结构、event_queues）
    background_tasks.add_task(_run_pipeline_task, task_id, payload, settings, request.app.state)
    return TaskResponse(
        task_id=task_id,
        status=TaskStatus.RUNNING,
        message="任务已提交，正在抓取并整理数据",
    )


@router.get("/task/{task_id}/stream")
async def stream_task_events(
    task_id: str,
    request: Request,
    task_store: TaskStore = Depends(_task_store),
) -> StreamingResponse:
    """SSE 端点：实时推送任务进度与分析结果。

    事件类型：
    - progress  {stage, percent, message} — 阶段进度
    - crawl_done {content_count, comment_count} — 爬取完成统计
    - comment   {data: ReviewItem} — 单条评论（normalize 后逐条推送）
    - agent_result {agent, data} — 各 LangGraph 节点完成后的分析结果
    - done      {task_id} — 任务全部完成
    - error     {message} — 任务失败
    """
    record = task_store.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="task not found")

    event_queues: dict[str, stdlib_queue.Queue] = getattr(
        request.app.state, "task_event_queues", {}
    )
    event_q: stdlib_queue.Queue | None = event_queues.get(task_id)

    async def generator():
        # 任务已完成但客户端才连上（队列已清理或未创建）
        if event_q is None:
            if record.status == TaskStatus.SUCCESS:
                event = {"type": "done", "task_id": task_id}
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            elif record.status == TaskStatus.FAILED:
                event = {"type": "error", "message": record.error_message or "任务失败"}
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            return

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = event_q.get_nowait()
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("type") in ("done", "error"):
                        break
                except stdlib_queue.Empty:
                    await asyncio.sleep(0.1)
        finally:
            # SSE 流结束后清理队列，防止内存泄漏
            event_queues.pop(task_id, None)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/task/{task_id}", response_model=AnalyzeOutput | TaskResponse)
def get_task_result(
    task_id: str,
    settings: Settings = Depends(get_settings),
    task_store: TaskStore = Depends(_task_store),
) -> AnalyzeOutput | TaskResponse:
    """获取任务状态与结果。"""
    record = task_store.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="task not found")
    if record.status != TaskStatus.SUCCESS:
        return TaskResponse(
            task_id=task_id,
            status=record.status,
            error_code=record.error_code,
            message=record.error_message,
        )
    processed_file = record.result.get("processed_file")
    if not processed_file:
        return TaskResponse(
            task_id=task_id,
            status=TaskStatus.RUNNING,
            message="结果生成中，请稍后重试",
        )
    result_file = Path(processed_file)
    if not result_file.exists():
        return TaskResponse(
            task_id=task_id,
            status=TaskStatus.FAILED,
            error_code=ErrorCode.INTERNAL_ERROR,
            message="结果文件不存在",
        )
    payload = json.loads(result_file.read_text(encoding="utf-8"))
    return AnalyzeOutput.model_validate(payload)


@router.get("/task/{task_id}/meta", response_model=dict[str, Any])
def get_task_meta(
    task_id: str,
    settings: Settings = Depends(get_settings),
    task_store: TaskStore = Depends(_task_store),
) -> dict[str, Any]:
    record = task_store.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="task not found")
    result = record.result or {}
    return {
        "task_id": task_id,
        "status": record.status,
        "error_code": record.error_code,
        "message": record.error_message,
        "processed_file": result.get("processed_file", ""),
        "chroma_counts": result.get("chroma_counts", {}),
        "vision_analysis": result.get("vision_analysis", {}),
        "params": record.params,
        "updated_at": record.updated_at.isoformat(),
    }


@router.get("/task/{task_id}/insights", response_model=dict[str, Any])
def get_task_insights(
    task_id: str,
    task_store: TaskStore = Depends(_task_store),
) -> dict[str, Any]:
    record, payload = _load_task_payload(task_id, task_store)
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    review_queue = _build_review_queue(payload)
    sentiment_bars = _build_sentiment_bars(payload)
    topic_cloud = _build_topic_cloud(payload)
    completed_tasks = len(
        [
            item
            for item in task_store.list_recent(limit=200)
            if item.status == TaskStatus.SUCCESS
        ]
    )
    efficiency = min(99, 65 + int(summary.get("feature_count", 0) / 2))
    result_meta = record.result or {}
    return {
        "task_id": task_id,
        "review_queue": review_queue,
        "comment_selection_meta": payload.get("comment_selection_meta", {}),
        "comment_ads_status": result_meta.get("comment_ads_status", "pending"),
        "comment_ads_progress": {
            "done": int(result_meta.get("comment_ads_done", 0)),
            "total": int(result_meta.get("comment_ads_total", 0)),
        },
        "progress": _build_progress(record, payload),
        "analytics": {
            "kpis": {
                "comment_count": int(summary.get("comment_count", 0)),
                "content_count": int(summary.get("content_count", 0)),
                "dispatch_efficiency": efficiency,
                "completed_tasks": completed_tasks,
            },
            "sentiment_bars": sentiment_bars,
            "topic_cloud": topic_cloud,
            "insight": "高意向评论集中在’真实体验+效果对比’语义簇，建议优先派发该类文案。",
        },
    }


@router.get("/tasks/recent", response_model=list[dict[str, Any]])
def get_recent_tasks(
    limit: int = 10,
    task_store: TaskStore = Depends(_task_store),
) -> list[dict[str, Any]]:
    records = task_store.list_recent(limit=limit)
    return [
        {
            "task_id": item.task_id,
            "status": item.status,
            "updated_at": item.updated_at.isoformat(),
            "error_message": item.error_message,
            "params": item.params,
        }
        for item in records
    ]


@router.get("/agents/state-schema", response_model=GlobalAgentState)
def get_agent_state_schema() -> GlobalAgentState:
    """返回各 Agent 联调使用的全局状态模板。"""
    return _build_state_template()


@router.post("/agents/vision/run", response_model=VisionAnalysis)
def run_vision_agent(
    payload: VisionRunRequest,
    settings: Settings = Depends(get_settings),
) -> VisionAnalysis:
    """单独执行 Agent2 视觉分析。"""
    return VisionAgent(settings).analyze(payload.media_paths)


@router.post("/agents/context/run", response_model=NLPAnalysis)
def run_context_agent(payload: ContextRunRequest) -> NLPAnalysis:
    """单独执行 Agent3 评论区语境分析。"""
    return ContextAgent().analyze(payload.comments, payload.product_info)


@router.post("/agents/rag/run", response_model=list[str])
def run_rag_agent(payload: RagRunRequest) -> list[str]:
    """单独执行 Agent4 检索接口。"""
    return RagAgent().retrieve(payload.vision_analysis, payload.nlp_analysis, payload.top_k)


@router.post("/agents/copywriter/run", response_model=list[AdDraft])
def run_copywriter_agent(payload: CopywriterRunRequest) -> list[AdDraft]:
    """单独执行 Agent5 文案生成接口。"""
    return CopywriterAgent().generate(
        payload.request_info,
        payload.vision_analysis,
        payload.nlp_analysis,
        payload.rag_references,
        payload.styles,
    )
