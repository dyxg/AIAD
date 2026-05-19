# AIAD (AI Advertising Discovery)

AIAD 是一个面向广告洞察与评论区软植入生成的实验项目，当前仓库包含：

- FastAPI 后端
- LangGraph 工作流编排
- Agent 2 视觉分析
- Agent 3 评论区语境分析
- Agent 4 RAG / Memory
- Agent 5 文案生成
- Vue 3 + Vite 前端

## 项目结构

```text
AIAD/
├─ app/                    # 后端应用
│  ├─ api/                 # API 路由
│  ├─ core/                # 配置与日志
│  ├─ models/              # Pydantic 数据模型
│  ├─ services/            # 各 Agent 与基础服务
│  └─ workflows/           # LangGraph 工作流
├─ assets/seeds/           # Agent 4 预置种子
├─ docs/                   # 协作文档
├─ frontend/               # Vue 3 + Vite 前端
├─ tests/                  # 测试
├─ vendor/MediaCrawler/    # 第三方爬虫
├─ data/raw/               # 原始抓取数据
├─ data/processed/         # 处理后结果
└─ scripts/                # 初始化与辅助脚本
```

## 克隆仓库

```bash
git clone --recurse-submodules https://github.com/TommyTay0712/AIAD.git
cd AIAD
```

如果已经克隆过主仓库：

```bash
git submodule sync --recursive
git submodule update --init --recursive
```

## 后端启动

推荐先复制环境变量模板：

```bash
# Linux / macOS
cp .env.example .env
# Windows PowerShell
Copy-Item .env.example .env
```

激活 conda 环境后安装依赖并启动后端：

```bash
# Linux / macOS（激活后 python 指向正确解释器）
conda activate aiad
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

```powershell
# Windows PowerShell（使用完整解释器路径，无需激活）
E:\AIAD\.conda\aiad\python.exe -m pip install -r requirements.txt
E:\AIAD\.conda\aiad\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

接口文档：

- Swagger: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

## 前端启动

```bash
cd frontend
npm install
npx vite --host 0.0.0.0 --port 5173
```

前端页面：`http://127.0.0.1:5173/`

## Conda 环境

仓库提供统一环境文件：

```bash
conda env create -f environment.aiad.yml
conda activate aiad
```

环境已存在时可更新：

```bash
conda env update -f environment.aiad.yml --prune
conda activate aiad
```

## Agent 4 初始化

Windows PowerShell:

```powershell
.\scripts\bootstrap_agent4.ps1 -Python E:\AIAD\.conda\aiad\python.exe
```

验证 Agent 4 状态：

```powershell
# Windows PowerShell
E:\AIAD\.conda\aiad\python.exe -m app.services.memory.cli status
E:\AIAD\.conda\aiad\python.exe -m app.services.memory.cli probe tests/memory/fixtures/mock_global_state_beach.json
```

## MediaCrawler 调试

建议使用项目内可写浏览器目录：

```bash
export PLAYWRIGHT_BROWSERS_PATH=.ms-playwright
```

二维码登录示例：

```powershell
# Windows PowerShell（MediaCrawler 专属解释器）
E:\AIAD\.conda\mediacrawler\python.exe main.py --platform xhs --lt qrcode --type search --keywords 美食 --headless false --save_data_option jsonl --save_data_path data/raw/xhs_real
```

## 联调文档

- 智能体接口与并行开发规范：`docs/智能体接口与并行开发规范.md`
- 项目分工与路线规划：`docs/项目分工与路线规划.md`

## 环境变量

复制 `.env.example` 后按需修改，主要包括：

- 基础路径：`MEDIA_CRAWLER_DIR`、`CRAWLER_OUTPUT_DIR`、`PROCESSED_OUTPUT_DIR`
- Python 与浏览器：`AIAD_PYTHON_EXE`、`MEDIACRAWLER_PYTHON_EXE`、`PLAYWRIGHT_BROWSERS_PATH`
- 日志与任务：`LOGS_DIR`、`LOG_LEVEL`、`TASK_STORE_FILE`
- LLM：`LLM_PROVIDER`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`
- Vision：`VISION_PROVIDER`、`VISION_MODEL`、`VISION_API_BASE`、`VISION_API_KEY`
- Agent 4：`AGENT4_PERSIST_DIR`、`AGENT4_SEED_DIR`、`AGENT4_EMBEDDING_MODEL`

## ModelScope OpenAI 兼容范式（Agent 2/3/5）

当前项目的 AI Agent 默认采用 OpenAI 兼容调用方式：

- `base_url`: `https://api-inference.modelscope.cn/v1`
- `model`: `Qwen/Qwen3.5-397B-A17B`
- `api_key`: 从环境变量读取（不写死在代码里）

示例（请勿在代码中明文写 Token）：

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="https://api-inference.modelscope.cn/v1",
    api_key=os.getenv("MODELSCOPE_API_KEY", ""),
)
```

注意：

- `base_url` 不要带反引号、前后空格或引号嵌套。
- 根地址直接访问可能返回 404，正常请求应走 `/chat/completions` 等 API 路径。

### API Key 替换步骤

1. 复制配置模板：`cp .env.example .env`（Windows 可手动复制）。
2. 打开 `.env`，把以下占位符替换为你的 ModelScope Token：
   - `LLM_API_KEY=YOUR_MODELSCOPE_API_KEY`
   - `VISION_API_KEY=YOUR_MODELSCOPE_API_KEY`
3. 不要提交 `.env` 到仓库，确保 Token 仅保存在本地。
4. 重启后端服务使新环境变量生效。

如果你曾在聊天、提交记录或日志中暴露过旧 Token，建议立即在 ModelScope 控制台吊销并重新生成。

## 常用接口

### 主流程

- `POST /api/ad-intel/run`
- `GET /api/ad-intel/task/{task_id}`
- `GET /api/ad-intel/task/{task_id}/meta`
- `GET /api/ad-intel/task/{task_id}/insights`

`/insights` 会返回后端筛选后的评论审核队列。任务3以后，系统保留原始 `comment_table`，
同时新增 `priority_comment_table` 和 `comment_selection_meta`：后端优先选择高点赞帖子中的高点赞评论，
并尽量每个帖子先只选一条评论。现有 `/run` 主流程仍保留 MediaCrawler 调用能力；
任务3的自动化测试不启动 MediaCrawler。前端只展示并刷新该优先队列，不发送真实评论，只提供 AI 文案复制。

### Agent 联调

- `GET /api/ad-intel/agents/state-schema`
- `POST /api/ad-intel/agents/vision/run`
- `POST /api/ad-intel/agents/context/run`
- `POST /api/ad-intel/agents/rag/run`
- `POST /api/ad-intel/agents/copywriter/run`

## 本地校验

```powershell
# Windows PowerShell
E:\AIAD\.conda\aiad\python.exe -m pytest tests -q
E:\AIAD\.conda\aiad\python.exe -m mypy app tests
```

```bash
# Linux / macOS（conda activate aiad 后）
python -m pytest tests -q
python -m mypy app tests
```
