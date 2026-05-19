from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class TaskStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class ErrorCode(str, Enum):
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    INVALID_INPUT = "INVALID_INPUT"
    CRAWLER_ERROR = "CRAWLER_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class RunRequest(BaseModel):
    """分析请求入参。"""

    ad_type: str = Field(min_length=1)
    post_url: str = Field(default="")
    product_info: str = Field(default="")
    target_style: str = Field(default="测评风")
    keywords: list[str] = Field(default_factory=list)
    platform: str = Field(default="xhs")
    limit: int = Field(default=20, ge=1, le=200)
    max_comments_per_note: int = Field(default=10, ge=1, le=200)
    enable_media_download: bool = Field(default=False)
    time_range: str = Field(default="")

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    error_code: ErrorCode | None = None
    message: str = ""


class VisionAnalysis(BaseModel):
    scene: str = ""
    vibe: str = ""
    detected_items: list[str] = Field(default_factory=list)
    people_emotions: list[str] = Field(default_factory=list)
    visual_highlights: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    source_media_count: int = 0
    model_provider: str = ""
    model_name: str = ""


class RawComment(BaseModel):
    user: str = ""
    content: str = ""
    likes: int = 0


class RequestInfo(BaseModel):
    post_url: str = ""
    product_info: str = ""
    target_style: str = ""


class RawData(BaseModel):
    post_content: str = ""
    media_paths: list[str] = Field(default_factory=list)
    comments: list[RawComment] = Field(default_factory=list)


class NLPAnalysis(BaseModel):
    main_emotion: str = ""
    pain_points: list[str] = Field(default_factory=list)
    language_style: str = ""
    ad_angles: list[str] = Field(default_factory=list)
    keyword_summary: list[str] = Field(default_factory=list)


class AdDraft(BaseModel):
    style: str = ""
    content: str = ""


class GlobalAgentState(BaseModel):
    request_info: RequestInfo = Field(default_factory=RequestInfo)
    raw_data: RawData = Field(default_factory=RawData)
    vision_analysis: VisionAnalysis = Field(default_factory=VisionAnalysis)
    nlp_analysis: NLPAnalysis = Field(default_factory=NLPAnalysis)
    rag_references: list[str] = Field(default_factory=list)
    final_ads: list[AdDraft] = Field(default_factory=list)
    review_score: int = 0


class VisionRunRequest(BaseModel):
    media_paths: list[str] = Field(default_factory=list)


class ContextRunRequest(BaseModel):
    comments: list[RawComment] = Field(default_factory=list)
    product_info: str = ""


class RagRunRequest(BaseModel):
    vision_analysis: VisionAnalysis = Field(default_factory=VisionAnalysis)
    nlp_analysis: NLPAnalysis = Field(default_factory=NLPAnalysis)
    top_k: int = Field(default=5, ge=1, le=10)


class CopywriterRunRequest(BaseModel):
    request_info: RequestInfo = Field(default_factory=RequestInfo)
    vision_analysis: VisionAnalysis = Field(default_factory=VisionAnalysis)
    nlp_analysis: NLPAnalysis = Field(default_factory=NLPAnalysis)
    rag_references: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)


class AnalyzeOutput(BaseModel):
    summary: dict[str, Any]
    content_table: list[dict[str, Any]]
    comment_table: list[dict[str, Any]]
    priority_comment_table: list[dict[str, Any]] = Field(default_factory=list)
    comment_selection_meta: dict[str, Any] = Field(default_factory=dict)
    feature_table: list[dict[str, Any]]
    vision_analysis: VisionAnalysis = Field(default_factory=VisionAnalysis)
    request_info: dict[str, Any] = Field(default_factory=dict)
    global_state: dict[str, Any] = Field(default_factory=dict)
    prompt_bundle: dict[str, Any] = Field(default_factory=dict)
    llm_result: dict[str, Any] = Field(default_factory=dict)
    final_ads: list[dict[str, Any]] = Field(default_factory=list)
    review_score: int = Field(default=0)


class TaskRecord(BaseModel):
    task_id: str
    created_at: datetime
    updated_at: datetime
    status: TaskStatus
    params: dict[str, Any]
    output_files: dict[str, str] = Field(default_factory=dict)
    error_code: ErrorCode | None = None
    error_message: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
