# 测试者勾选清单

> **用途**：第一次跑这套压测时，**逐项打钩**，确保不漏步骤。
>
> 总耗时预期 ≤ 30 分钟。卡哪一步先查 [`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md)。

---

## 阶段 1：环境准备（首次配置，10–15 分钟）

### 1.1 仓库

- [ ] 已克隆仓库（含子模块）
  ```bash
  git clone --recurse-submodules https://github.com/TommyTay0712/AIAD.git
  cd AIAD
  ```
- [ ] `vendor/MediaCrawler/main.py` 文件存在

### 1.2 Python 环境

- [ ] 已激活 Python 3.10+ 环境（conda / venv 任选）
- [ ] `python --version` ≥ 3.10
- [ ] 当前 `python` 指向激活后的解释器（不是系统 base）

### 1.3 后端依赖

- [ ] `python -m pip install -r requirements.txt` 完成（**首次较慢，3–10 分钟**）
- [ ] `python -c "import fastapi, uvicorn, langgraph, chromadb, openai; print('OK')"` 输出 `OK`

### 1.4 压测依赖

- [ ] `python -m pip install -r stress_tests/requirements.txt` 完成
- [ ] `python -m locust --version` 输出 2.31+ 版本号

### 1.5 配置文件（可选）

- [ ] `.env` 已从 `.env.example` 复制（即使不填 LLM key 也建议复制，避免后端启动告警）
- [ ] **如果要测 LLM 端点**：`.env` 里填好 `LLM_API_KEY`、`VISION_API_KEY`
- [ ] **如果要测 POST /run 真实爬取**：`.env` 里填好 MediaCrawler 凭据，并跑 `python scripts/bootstrap_agent4.py`

---

## 阶段 2：启动后端（2 分钟）

新开一个终端（保持环境激活）。

- [ ] 跑 `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
- [ ] 终端打印 `Uvicorn running on http://127.0.0.1:8000`
- [ ] 浏览器 / curl 访问 `http://127.0.0.1:8000/api/ad-intel/agents/state-schema` 返回 JSON

> ⚠️ 该终端**整个测试期间保持运行**，跑完才关。

---

## 阶段 3：smoke 烟雾测试（3 分钟）

### 3.1 跑 smoke

新开第三个终端（环境激活）。

- [ ] 跑命令（按平台选）：
  ```bash
  # Linux / macOS
  python stress_tests/smoke.py --host http://127.0.0.1:8000 --skip-llm --skip-heavy

  # Windows PowerShell
  python stress_tests\smoke.py --host http://127.0.0.1:8000 --skip-llm --skip-heavy
  ```
- [ ] 看到 `PASS: required endpoints reachable`
- [ ] 退出码为 0（`echo $?` / `$LASTEXITCODE`）

### 3.2 出错处理

- [ ] 没出错跳过此项
- [ ] 如果 ConnectError：回阶段 2 检查后端
- [ ] 如果 500：查 [`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md#smoke-不通过)

---

## 阶段 4：第一次 Locust 压测（5 分钟）

选 **A 或 B 之一**，新手建议先 A。

### A：UI 模式（推荐入门）

- [ ] 跑命令（按平台选环境变量语法）：
  ```bash
  # Linux / macOS / WSL
  LOCUST_SKIP_LLM=1 LOCUST_SKIP_HEAVY_RUN=1 LOCUST_SKIP_SSE=1 \
    python -m locust -f stress_tests/locustfile.py --host http://127.0.0.1:8000

  # Windows PowerShell
  $env:LOCUST_SKIP_LLM="1"; $env:LOCUST_SKIP_HEAVY_RUN="1"; $env:LOCUST_SKIP_SSE="1"
  python -m locust -f stress_tests\locustfile.py --host http://127.0.0.1:8000
  ```
- [ ] 终端打印 `Starting web interface at http://0.0.0.0:8089`
- [ ] 浏览器打开 `http://127.0.0.1:8089` 看到 Locust UI
- [ ] 填 Number of users = `10`, Ramp up = `2`, 点 **Start swarming**
- [ ] **Statistics** Tab 看到请求数在涨、Failures 列保持 0
- [ ] 跑 30 秒后，UI 顶栏点 **STOP**
- [ ] 终端 `Ctrl+C` 关 Locust 进程

### B：headless 一键跑

- [ ] 跑命令：
  ```bash
  python -m locust --config stress_tests/conf/smoke.conf
  ```
- [ ] 30 秒后自动结束
- [ ] 终端打印 `Aggregated` 一行汇总数字（# fails 应为 0）

### 共同检查

- [ ] **没有红色 ERROR 日志**
- [ ] 至少看到这些端点被打到：
  - `GET /agents/state-schema`
  - `GET /tasks/recent`
  - `GET /task/{id}` / `GET /task/{id}/meta` / `GET /task/{id}/insights`
  - `POST /agents/rag/run`

---

## 阶段 5：查看报告（2 分钟）

只有 **headless 模式**或显式 `--html` 才有报告产物。

- [ ] `stress_tests/.reports/smoke.html` 文件存在
- [ ] 浏览器双击打开，看到完整图表
- [ ] 报告里 `# fails` 列全为 0
- [ ] p99 延迟 < 100ms（cold-start 兜底，本地 localhost）

如果用 UI 模式跑的，回到 Web UI 的 **Download Data** 标签，能下载同样 4 个 CSV。

---

## 阶段 6：升级压测强度（可选，5–10 分钟）

如果阶段 4 一切顺利，可以试更大强度：

- [ ] 跑 `python -m locust --config stress_tests/conf/agent4_only.conf`（30 用户 / 3 分钟，专压 RAG）
- [ ] 跑 `python -m locust --config stress_tests/conf/normal.conf`（50 用户 / 5 分钟，要 LLM key）
- [ ] 跑 `python -m locust --config stress_tests/conf/peak.conf`（200 用户 / 15 分钟，摸顶）

每跑完看一遍 `stress_tests/.reports/<level>.html` 的：

- [ ] # fails 列保持 < 1%
- [ ] RPS 曲线随用户数线性增长（不是早早平台化）
- [ ] 没有突然的 5xx 雪崩

---

## 阶段 7：清场（1 分钟）

测完了顺手清理：

- [ ] 关掉 Locust 终端
- [ ] 关掉 uvicorn 后端终端
- [ ] （可选）`stress_tests/.reports/` 想保留就 commit 一份基线，否则可删
- [ ] （可选）`data/chroma/` / `data/raw/` / `data/processed/` 是测试产生的中间数据，可清

```bash
# Linux / macOS
rm -rf stress_tests/.reports data/raw data/processed

# Windows PowerShell
Remove-Item -Recurse -Force stress_tests\.reports, data\raw, data\processed
```

---

## 完成标志

走完上述阶段，你可以确认：

- ✅ 后端能启动、能响应
- ✅ 压测代码本身可运行（无 import / 语法错误）
- ✅ 至少 60% 端点在压力下健康（读类 + Agent 4）
- ✅ 报告文件能输出、能阅读
- ✅ 任何"测不到的端点"都有明确原因（缺 LLM key / 缺爬虫凭据）

把这份勾完的清单 + `.reports/` 的 HTML 一起交给团队负责人即可。

---

## 反馈

发现 checklist 里某一步不准、命令报错、文档与现实不符——

直接在仓库提 issue 或 PR 评论里指出。文档跟代码一样需要持续维护。
