from __future__ import annotations

import importlib
import json
import logging
from types import ModuleType
from typing import Any

def _load_openai_module() -> ModuleType | None:
    try:
        return importlib.import_module("openai")
    except ImportError:  # pragma: no cover - 运行环境可选依赖
        return None


openai_module = _load_openai_module()

from app.core.config import Settings

logger = logging.getLogger(__name__)


class ContextNLPAgent:
    """Agent3: 评论区语境与情感智能体，基于大模型分析评论语境。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: Any | None = None
        llm_provider = settings.llm_provider.strip().lower()
        if llm_provider in {"", "disabled", "none", "null"} or not settings.llm_api_key.strip():
            self.model = settings.llm_model
            logger.warning("Agent3 未启用远程 LLM（provider 或 api_key 未配置），将使用默认语境输出")
            return
        self.client = (
            openai_module.OpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
            )
            if openai_module is not None
            else None
        )
        self.model = settings.llm_model
        if self.client is None:
            logger.warning("openai 依赖未安装，Agent3 将使用默认语境输出")
        else:
            logger.info("Agent3 初始化完成，使用模型: %s", self.model)

    def analyze_comments(self, comments: list[dict[str, Any]]) -> dict[str, Any]:
        """
        分析评论列表，返回结构化 NLP 分析结果。

        Args:
            comments: 评论列表，每个元素包含 content 字段。

        Returns:
            包含 main_emotion, pain_points, language_style 的字典。
        """
        # 提取评论文本
        comment_texts = [c.get("content", "") for c in comments if c.get("content")]
        if not comment_texts:
            logger.warning("无有效评论文本，返回默认语境")
            return self._get_default_context()

        sample_comments = "\n".join(comment_texts[:30])  # 限制数量防止超 token
        if self.client is None:
            return self._get_default_context()

        system_prompt = """
你是一个小红书评论区语境分析专家。小红书评论中常出现 `[doge]`、`[失望R]`、`[大笑R]` 等占位符，代表对应 Emoji，请结合上下文理解情绪。

分析以下评论样本，并以严格的 JSON 格式返回，不要包含任何额外文字：
{
  "main_emotion": "整体情感倾向描述，例如'积极询问，对产品有兴趣'",
  "pain_points": ["数组，列出用户最关心的3个具体问题，如'油腻吗？'、'敏感肌能用吗？'"],
  "language_style": "评论区的语言风格，如'玩梗调侃为主，姐妹互称'"
}
"""
        user_content = f"评论文本样本：\n{sample_comments}"

        try:
            logger.info("正在调用大模型分析 %d 条评论", len(comment_texts))
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=500,
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("Agent3 返回空响应")
            result = json.loads(content)
            logger.info("Agent3 分析成功")
            return result
        except Exception as e:
            logger.error("Agent3 调用失败: %s", e, exc_info=True)
            return self._get_default_context()

    def _get_default_context(self) -> dict[str, Any]:
        return {
            "main_emotion": "中性讨论",
            "pain_points": ["需求待挖掘"],
            "language_style": "日常交流",
        }
