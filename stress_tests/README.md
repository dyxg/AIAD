# 后端压力测试完整走查手册

> **目标读者**：第一次接触本压测套件的测试人员 / 团队负责人。
>
> **预期时长**：从克隆仓库到看到第一份压测报告 **≤ 30 分钟**。
>
> **平台兼容**：Windows / macOS / Linux 命令分别给出，全程纯 Python，不依赖 shell 脚本。

---

## 目录

1. [它能干嘛 / 不能干嘛](#1-它能干嘛--不能干嘛)
2. [前置条件](#2-前置条件)
3. [完整环境准备（首次配置）](#3-完整环境准备首次配置)
4. [验证后端可用：smoke 测试](#4-验证后端可用smoke-测试)
5. [跑第一次压测：Locust UI 模式](#5-跑第一次压测locust-ui-模式)
6. [无人值守：headless 模式 + 预设 conf](#6-无人值守headless-模式--预设-conf)
7. [测试场景剧本（5 个具体怎么压）](#7-测试场景剧本)
8. [报告产物与解读](#8-报告产物与解读)
9. [环境变量参考](#9-环境变量参考)
10. [架构与端点覆盖矩阵](#10-架构与端点覆盖矩阵)
11. [扩展与维护](#11-扩展与维护)
12. [更多资料](#12-更多资料)

> **遇到问题先查** [`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md)。
>
> **逐项勾选式快速走完** 见 [`CHECKLIST.md`](./CHECKLIST.md)。

---

## 1. 它能干嘛 / 不能干嘛

### ✅ 它测什么

- AIAD 后端 11 个 HTTP 端点的**吞吐 + 延迟分位 + 错误率**
- 关键并发逻辑（in-flight 去重、SSE 队列、文件锁、后台线程池）在压力下的退化行为
- 拟真用户旅程（提交 → 轮询 → 拉详情）的端到端延迟

### ❌ 它**不**测什么

| 不测 | 原因 | 应该谁测 |
|---|---|---|
| 业务结果对错（LLM 输出质量、检索召回准确率） | 压测目标是吞吐 + 延迟 | 单元 / 集成测试（`tests/`） |
| 鉴权、权限、防注入 | 后端目前无 auth | 接 auth 后另做 |
| 数据库故障注入 | 需要 chaos engineering | 后续阶段 |
| 跨地域延迟 | localhost 测不出 | staging / 生产环境真实压测 |

> **诚实声明**：如果你 **本地没有 LLM API key、没有 Chroma 数据、没有 MediaCrawler 凭据**，
> 你只能压到"读端点 + Agent 4 RAG"这部分（约占总端点 60%）。剩下的留给团队负责人在他的环境跑。
> 这是**预期行为**，不是 bug。

### 🛠️ 所用工具

- [**Locust**](https://locust.io)：Python 原生压测框架，自带 Web UI 和 HTML/CSV 报告
- 自研 `smoke.py`：不依赖 Locust 的烟雾脚本，验证端点连通性

---

## 2. 前置条件

| 项 | 最低版本 | 用途 |
|---|---|---|
| **Python** | 3.10+ | 后端 + 压测都用 |
| **磁盘空间** | 500 MB+ | 依赖 + 报告产物 |
| **内存** | 2 GB+ | Locust 高并发时 worker 占用 |
| **网络** | 能访问 PyPI | `pip install` 用 |

**可选但推荐**：
- conda（管理 Python 环境）
- 浏览器（看 Locust Web UI / HTML 报告）

**不强求**：
- LLM API key（无 key 时跳过 Agent 2/3/5 端点即可）
- Chroma 已灌种（无种子时 Agent 4 走 cold-start 兜底）
- MediaCrawler 凭据（无凭据时跳过 `POST /run` 即可）

---

## 3. 完整环境准备（首次配置）

按顺序走完即可。每一步都给出验证命令，跑出预期输出再进入下一步。

### 3.1 克隆仓库（含子模块）

```bash
git clone --recurse-submodules https://github.com/TommyTay0712/AIAD.git
cd AIAD
```

如果之前忘了 `--recurse-submodules`：

```bash
git submodule update --init --recursive
```

**验证**：

```bash
ls vendor/MediaCrawler/main.py
# 应输出该文件路径，不是 "No such file"
```

### 3.2 创建并激活 Python 环境

#### 方案 A：conda（推荐）

```bash
conda env create -f environment.aiad.yml
conda activate aiad
```

#### 方案 B：venv（无 conda 时用）

```bash
# Linux / macOS
python3.10 -m venv .venv
source .venv/bin/activate

# Windows PowerShell
py -3.10 -m venv .venv
.venv\Scripts\Activate.ps1
```

**验证**：

```bash
python --version
# 应输出 Python 3.10.x
```

> ⚠️ **后续命令以"`python` 已是 aiad 解释器"为前提**。如果你不激活环境而用绝对路径
> （如 `D:\conda\envs\aiad\python.exe`），把命令里的 `python` 替换成绝对路径即可。

### 3.3 安装后端依赖

```bash
python -m pip install -r requirements.txt
```

这一步会装 fastapi、uvicorn、langgraph、chromadb、openai、sentence-transformers 等。
**首次安装可能 3–10 分钟**（torch / sentence-transformers 包较大）。

**验证**：

```bash
python -c "import fastapi, uvicorn, langgraph, chromadb, openai; print('backend deps OK')"
# 期望：backend deps OK
```

### 3.4 安装压测依赖

```bash
python -m pip install -r stress_tests/requirements.txt
```

只装 3 个包：locust、httpx、faker。**1 分钟内**完成。

**验证**：

```bash
python -m locust --version
# 期望：locust 2.31.0+ （大版本号即可）
```

### 3.5 准备 `.env` 配置（可选，按你要测什么决定）

```bash
# Linux / macOS
cp .env.example .env
# Windows PowerShell
Copy-Item .env.example .env
```

打开 `.env`，根据你**这次要测什么**选择性配置：

| 要测什么 | 必填字段 | 不填会怎样 |
|---|---|---|
| 只压读端点 + Agent 4 RAG | 啥都不用填 | 完全可以跑 |
| 想压 Agent 2/3/5（LLM 直调） | `LLM_API_KEY`、`VISION_API_KEY` | 这 3 个端点会 5xx，需 `LOCUST_SKIP_LLM=1` 跳过 |
| 想压 `POST /run` 全流程 | 上面 + MediaCrawler 凭据 | 爬虫会失败，需 `LOCUST_SKIP_HEAVY_RUN=1` 跳过 |
| 想测 Agent 4 RAG 真实命中（不是 cold-start） | `AGENT4_*`（已有合理默认） + 灌种 | 不灌种返回空 list，仍 200 |

> **新手建议**：第一次先**啥都不填**，跑通"读端点 + Agent 4 cold-start"再慢慢扩展。

### 3.6 （可选）灌种 Agent 4 数据

只在你想看 RAG 命中（不是空 list）时做。**跳过此步压测仍能跑**，只是 Agent 4 永远返回空。

```bash
# Windows PowerShell
python scripts\bootstrap_agent4.py

# Linux / macOS
python scripts/bootstrap_agent4.py
```

**验证**：

```bash
python -m app.services.memory.cli status
# 期望：看到 chroma 集合统计，counts > 0
```

### 3.7 启动后端

**新开一个终端**（保持环境激活），跑：

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**验证**：终端打印类似：

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

或者**另开一个终端**用 curl / 浏览器访问：

```bash
curl http://127.0.0.1:8000/api/ad-intel/agents/state-schema
# 期望返回 JSON，不是 connection refused
```

### 3.8 环境检查清单（继续往下前确认）

- [ ] `python --version` ≥ 3.10
- [ ] `python -c "import locust"` 不报错
- [ ] 后端在 `http://127.0.0.1:8000` 跑着
- [ ] `curl /api/ad-intel/agents/state-schema` 返回 JSON
- [ ] 子模块 `vendor/MediaCrawler/main.py` 存在
- [ ] （可选）`.env` 已根据需求配置

全部 ✅ 才进入下一章。

---

## 4. 验证后端可用：smoke 测试

**为什么先跑 smoke**：在拉起 Locust 之前，先用一个一次性脚本快速验证：
1. 后端起着、能连
2. 路由都对
3. 我们构造的 payload 都被 Pydantic 接受
4. Agent 4 cold-start 兜底正常

`smoke.py` 不依赖 Locust，30 秒跑完。

### 4.1 最稳跑法（跳过外部依赖）

```bash
# Linux / macOS
python stress_tests/smoke.py --host http://127.0.0.1:8000 --skip-llm --skip-heavy

# Windows PowerShell
python stress_tests\smoke.py --host http://127.0.0.1:8000 --skip-llm --skip-heavy
```

### 4.2 期望输出

```
[smoke] target host = http://127.0.0.1:8000
[smoke] skip_llm    = True
[smoke] skip_heavy  = True

========================================================================
STATUS  TIME(ms)    REQ   NAME
------------------------------------------------------------------------
200     17.9        [REQ] GET /agents/state-schema
200     22.5        [REQ] GET /tasks/recent?limit=5
404     6.5               GET /task/{nonexistent}
200     3.4               POST /agents/rag/run
========================================================================
PASS: required endpoints reachable
```

退出码 **0** = 通过。出现 **1** 看 [`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md#smoke-不通过)。

### 4.3 启用更多端点（可选）

如果你 `.env` 已配 LLM key：

```bash
python stress_tests/smoke.py --host http://127.0.0.1:8000
# 不加任何 --skip-* 时会打 vision/context/copywriter/POST /run
```

期望除上面 4 个外还见：
- `200 POST /agents/vision/run`
- `200 POST /agents/context/run`
- `200 POST /agents/copywriter/run`（可能 30 秒级，因 LLM 慢）
- `200 POST /run (smoke, limit=1)`

> ⚠️ POST /run 会真的触发**爬虫 + LangGraph + LLM**，可能耗时数分钟。本压测脚本只关心
> 提交那一瞬间是不是 200，不等任务真完成。

---

## 5. 跑第一次压测：Locust UI 模式

UI 模式最适合**第一次跑**和**实时观察**。

### 5.1 启动 Locust

```bash
# 跳过依赖外部服务的端点（最稳）
# Linux / macOS
LOCUST_SKIP_LLM=1 LOCUST_SKIP_HEAVY_RUN=1 LOCUST_SKIP_SSE=1 \
  python -m locust -f stress_tests/locustfile.py --host http://127.0.0.1:8000

# Windows PowerShell
$env:LOCUST_SKIP_LLM="1"
$env:LOCUST_SKIP_HEAVY_RUN="1"
$env:LOCUST_SKIP_SSE="1"
python -m locust -f stress_tests\locustfile.py --host http://127.0.0.1:8000
```

终端会打印：

```
[INFO] Starting web interface at http://0.0.0.0:8089 (accepting connections from all network interfaces)
[INFO] Starting Locust 2.x.x
```

### 5.2 打开 Web UI

浏览器访问 [http://127.0.0.1:8089](http://127.0.0.1:8089)。

填表：

| 字段 | 推荐值（首次） | 说明 |
|---|---|---|
| Number of users | `10` | 模拟并发用户数 |
| Ramp up | `2` | 每秒新增几个用户（10/2=5 秒爬到峰值） |
| Host | （已填） | 别动，命令行已设 |

点 **Start swarming**。

### 5.3 实时观察 4 个 Tab

- **Statistics**：每个端点的 # reqs / # fails / 中位数 / p95 / p99 / RPS
- **Charts**：曲线图（RPS、响应时间、用户数随时间）
- **Failures**：失败请求详情（error message + 计数）
- **Workers**：分布式时才有用，单机略过

### 5.4 优雅停止

UI 顶栏点 **STOP**（不是直接关浏览器，否则 Locust 进程还在）。然后回终端 `Ctrl+C` 关进程。

### 5.5 期望看到的数字（10 用户 / 30 秒）

参考实测值（本机 localhost、cold-start 兜底）：

| 端点 | RPS | p99 |
|---|---|---|
| `GET /agents/state-schema` | ~0.1 | < 10ms |
| `GET /tasks/recent` | ~0.4 | < 20ms |
| `GET /task/{id}` (404) | ~0.7 | < 20ms |
| `GET /task/{id}/meta` | ~0.9 | < 20ms |
| `GET /task/{id}/insights` | ~0.4 | < 20ms |
| `POST /agents/rag/run` | ~1.5 | < 30ms |

总 RPS ~5、0 fail、延迟 < 35ms 就算正常。

---

## 6. 无人值守：headless 模式 + 预设 conf

适合**团队负责人一键跑** / **CI 跑** / **想把结果存档对比**。

### 6.1 4 种预设强度

| 配置 | 用户数 | 时长 | 用途 |
|---|---|---|---|
| `smoke.conf` | 5 | 30s | 跑通即可 |
| `agent4_only.conf` | 30 | 3min | 只压 Agent 4 RAG，不依赖 LLM |
| `normal.conf` | 50 | 5min | 日常压测 |
| `peak.conf` | 200 | 15min | 摸顶 |

### 6.2 跑法

```bash
# Linux / macOS
python -m locust --config stress_tests/conf/smoke.conf

# Windows PowerShell
python -m locust --config stress_tests\conf\smoke.conf
```

跑完会在 `stress_tests/.reports/` 下产出文件，见第 8 章。

### 6.3 临时 override 配置参数

```bash
# 还是用 smoke.conf 但延长到 2 分钟
python -m locust --config stress_tests/conf/smoke.conf --run-time 2m

# 改 host 命中 staging
python -m locust --config stress_tests/conf/normal.conf --host https://staging.aiad.example.com
```

CLI 参数会覆盖 conf 文件里的同名字段。

---

## 7. 测试场景剧本

下面是 5 个**具体怎么压**的剧本，每个给出完整命令链 + 期望结果。
**按这个顺序走，第一次能 100% 跑通**。

### 剧本 A：本地最小集（最稳，5 分钟）

**测什么**：读端点 5 个 + Agent 4 RAG。**完全不需要 LLM key / 爬虫凭据**。

```bash
# 终端 1（保持运行）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 终端 2
LOCUST_SKIP_LLM=1 LOCUST_SKIP_HEAVY_RUN=1 LOCUST_SKIP_SSE=1 \
  python -m locust --config stress_tests/conf/smoke.conf
```

**期望**：5 用户 / 30 秒 / 0 fails / 总 RPS 5–10 / 报告输出 `.reports/smoke.html`。

### 剧本 B：Agent 4 RAG 专项（团队专项压测）

**测什么**：高并发下 Chroma 检索的吞吐与延迟。

```bash
LOCUST_SKIP_LLM=1 LOCUST_SKIP_HEAVY_RUN=1 LOCUST_SKIP_SSE=1 \
  python -m locust --config stress_tests/conf/agent4_only.conf
```

**期望**：30 用户 / 3 分钟，主要打 `POST /agents/rag/run` 两个变体（默认 top_k 与 top_k=10）。
报告关注 p95 < 100ms（cold-start 兜底）/ < 500ms（真有 Chroma 数据）。

### 剧本 C：常规全量压测（要 LLM key）

**测什么**：所有 11 个端点的常规负载。

**前置**：`.env` 里配好 `LLM_API_KEY` 和 `VISION_API_KEY`（同一个 ModelScope token 即可）。

```bash
# 不设 LOCUST_SKIP_*，全开
python -m locust --config stress_tests/conf/normal.conf
```

**期望**：50 用户 / 5 分钟。LLM 端点延迟会到几秒级（正常）。`.reports/normal.html` 产出。

### 剧本 D：SSE 长连接专项

**测什么**：后端能维持多少并发 SSE 连接。

```bash
# 只跑 SSE 用户类
python -m locust -f stress_tests/locustfile.py \
  --host http://127.0.0.1:8000 \
  --users 50 --spawn-rate 5 --run-time 2m \
  --headless --tags sse \
  --html stress_tests/.reports/sse.html
```

**期望**：50 个并发 SSE 连接保持 2 分钟。监控后端进程的 RSS 内存和 CPU。
**关注点**：`task_event_queues` 字典是否随连接数线性增长（说明清理 OK）还是积压（内存泄漏）。

### 剧本 E：in-flight 去重压测（专项）

**测什么**：所有用户用同一关键词打 `POST /run`，验证后端去重锁。

**前置**：MediaCrawler 凭据已配，或后端有 mock 模式。

```bash
# 启用提交场景，侧重相同关键词
python -m locust -f stress_tests/locustfile.py \
  --host http://127.0.0.1:8000 \
  --users 20 --spawn-rate 2 --run-time 3m \
  --headless --tags submit,dedup \
  --html stress_tests/.reports/dedup.html
```

**期望**：第一个请求触发爬虫，后续请求等待 owner 完成 → **后端日志只能看到 1 次实际爬取**，
但 Locust 应记录 20+ 次提交请求都返回 200。错就错在重复爬。

---

## 8. 报告产物与解读

### 8.1 报告文件

跑完任意 conf 都会在 `stress_tests/.reports/<level>/` 下输出：

| 文件 | 内容 | 用途 |
|---|---|---|
| `<level>.html` | 完整 HTML 报告 | 浏览器打开看图、分享 |
| `<level>_stats.csv` | 各端点最终指标 | 写报告 / 对比多次跑 |
| `<level>_failures.csv` | 错误详情 | 排查 fail 类型 |
| `<level>_stats_history.csv` | 时间序列 | 画自定义曲线 |
| `<level>_exceptions.csv` | Python 异常栈 | 调试 task 代码 |

### 8.2 关键指标怎么看

| 指标 | 含义 | 健康阈值 |
|---|---|---|
| **# reqs** | 总请求数 | 越多越好（说明跑出量了） |
| **# fails** | 失败数 | < 1% 视为正常 |
| **Median (50%)** | 一半请求快于此值 | 读端点 < 50ms / 提交 < 1s |
| **95%** | 95% 请求快于此值 | 读端点 < 500ms / 提交 < 5s |
| **99%** | 99% 请求快于此值 | 看是否有长尾 |
| **RPS** | 每秒请求数 | 关心绝对值 + 是否随用户数增长 |
| **failures/s** | 每秒失败数 | 跟 RPS 比看比例 |

### 8.3 看曲线（Charts 页 / `_stats_history.csv`）

- **RPS 曲线**：平稳 = 后端没瓶颈；锯齿 / 下跌 = 过载
- **响应时间曲线**：平稳 = 健康；阶梯式上升 = 资源耗尽（连接池 / 线程池满）
- **用户曲线**：应该按 ramp-up 曲线匀速爬，不应该突然平台化

### 8.4 失败排查

`_failures.csv` 里 `Error` 列告诉你为啥挂：

| Error 关键词 | 含义 | 处理 |
|---|---|---|
| `Connection refused` | 后端没起 | 检查 uvicorn |
| `Read timeout` | 后端 hang | 看后端日志 |
| `server error 500` | 后端崩 | 看后端 traceback |
| `validation error` | 入参不合法 | 上报 issue（说明 payload 工厂有 bug） |
| `Unknown` | 网络 / 代理问题 | 检查防火墙 |

---

## 9. 环境变量参考

### 9.1 Locust 控制开关

| 变量 | 默认 | 含义 | 建议场景 |
|---|---|---|---|
| `LOCUST_SKIP_LLM` | `0` | `1` 时 Agent2/3/5 用户类 weight=0 | 缺 LLM key 时设 1 |
| `LOCUST_SKIP_SSE` | `0` | `1` 时 SSE 用户类 weight=0 | 不测 SSE 时设 1 |
| `LOCUST_SKIP_HEAVY_RUN` | `0` | `1` 时 SubmitOnlyUser/FullJourneyUser weight=0 | 缺爬虫凭据时设 1 |
| `LOCUST_SUBMIT_LIMIT` | `1` | POST /run 的 `limit`（爬取条数） | 真实压测可调到 5–10 |
| `LOCUST_SUBMIT_MAX_COMMENTS` | `1` | POST /run 的 `max_comments_per_note` | 同上 |
| `LOCUST_TASK_IDS` | _（占位）_ | 已知真实 task_id 列表，逗号分隔 | 让读端点压真数据 |
| `LOCUST_JOURNEY_MAX_POLLS` | `10` | FullJourneyUser 单次最多轮询次数 | 任务慢时调高 |
| `LOCUST_SSE_MAX_EVENTS` | `100` | 单条 SSE 连接最多读多少事件就主动断 | 长测时调高 |
| `LOCUST_SSE_MAX_SECONDS` | `30` | 单条 SSE 连接最长保持秒数 | 同上 |

### 9.2 后端配置（影响哪些端点能跑）

来自 `.env`，详见 `.env.example`。**压测脚本本身不读这些**，但后端读，读不到时端点会挂。

| 字段 | 影响 | 缺失时 |
|---|---|---|
| `LLM_API_KEY` | Agent 3/5 LLM 调用 | 端点 5xx |
| `VISION_API_KEY` | Agent 2 视觉分析 | 端点 5xx 或 fallback |
| `CHROMA_PERSIST_DIR` | 关键词缓存、Agent 4 RAG | 自动建空目录 |
| `AGENT4_PERSIST_DIR` | Agent 4 自有 Chroma | 自动建空目录 |
| `AGENT4_SEED_DIR` | Agent 4 种子数据来源 | RAG 返回空 list |
| `MEDIA_CRAWLER_DIR` | POST /run 触发爬虫 | 爬虫失败 |

---

## 10. 架构与端点覆盖矩阵

### 10.1 文件结构

```
stress_tests/
├── README.md                    # 本文档
├── TROUBLESHOOTING.md           # 故障排查
├── CHECKLIST.md                 # 测试者勾选表
├── requirements.txt             # locust + httpx + faker
├── locustfile.py                # 入口：聚合 8 个 User 类
├── smoke.py                     # 不依赖 Locust 的烟雾脚本
├── conf/
│   ├── smoke.conf               # 5 用户 / 30s
│   ├── normal.conf              # 50 用户 / 5min
│   ├── peak.conf                # 200 用户 / 15min
│   └── agent4_only.conf         # 30 用户 / 3min（不依赖 LLM）
├── data/
│   ├── ad_types.txt             # 20 个广告品类
│   ├── sample_comments.json     # 10 条评论样本
│   └── sample_state.json        # vision + nlp 分析样本
└── scenarios/
    ├── _common.py               # 路径常量、payload 工厂、env 解析
    ├── read_only.py             # ReadOnlyUser（5 个 GET）
    ├── analyze_submit.py        # SubmitOnlyUser（POST /run）
    ├── agents_direct.py         # Agent2/3/4/5 直调（4 个 User）
    ├── sse_listener.py          # SseListenerUser（SSE 长连接）
    └── full_journey.py          # FullJourneyUser（提交→轮询→详情）
```

### 10.2 端点 ↔ 场景 ↔ 标签

每个端点用 `@tag(...)` 打了分类，可用 `--tags` / `--exclude-tags` 筛选：

| 端点 | scenario | tags | LLM 依赖 |
|---|---|---|---|
| `GET /agents/state-schema` | read_only | `read,schema` | ❌ |
| `GET /tasks/recent` | read_only | `read,list` | ❌ |
| `GET /task/{id}` | read_only / journey | `read,detail` | ❌ |
| `GET /task/{id}/meta` | read_only / journey | `read,detail` | ❌ |
| `GET /task/{id}/insights` | read_only / journey | `read,insights` | ❌ |
| `POST /run` | analyze_submit / journey | `submit,heavy` | 间接 |
| `POST /run`（同关键词）| analyze_submit | `submit,dedup` | 间接 |
| `GET /task/{id}/stream` | sse_listener | `sse,stream` | ❌ |
| `POST /agents/vision/run` | agents_direct | `agent2,vision,llm` | ✅ |
| `POST /agents/context/run` | agents_direct | `agent3,context,llm` | ✅ |
| `POST /agents/rag/run` | agents_direct | `agent4,rag,chroma` | ❌ |
| `POST /agents/copywriter/run` | agents_direct | `agent5,copywriter,llm` | ✅ |
| 完整旅程 | full_journey | `journey` | 间接 |

### 10.3 用 `--tags` 精准筛选

```bash
# 只压所有读端点
python -m locust -f stress_tests/locustfile.py --tags read --host http://...

# 只压 Agent 4
python -m locust -f stress_tests/locustfile.py --tags agent4 --host http://...

# 排除所有 LLM 端点
python -m locust -f stress_tests/locustfile.py --exclude-tags llm --host http://...

# 多 tag 并集
python -m locust -f stress_tests/locustfile.py --tags read,journey --host http://...
```

---

## 11. 扩展与维护

### 11.1 新增端点

1. 在 `scenarios/_common.py` 加：
   - 路径常量 `PATH_XXX`
   - payload 工厂 `make_xxx_payload()`（如有 body）
2. 在合适的 scenario 文件里加 `@task` 方法：
   ```python
   @tag("xxx", "newfeature")
   @task(weight)
   def call_new_endpoint(self):
       with self.client.post(PATH_XXX, json=make_xxx_payload(),
                              name="POST /xxx", catch_response=True) as r:
           ...
   ```
3. 不需要改 `locustfile.py`

### 11.2 新增场景类型

1. 在 `scenarios/` 下新建 `xxx.py` 写 `XxxUser`
2. 在 `locustfile.py` 顶部加 `from scenarios.xxx import XxxUser  # noqa: F401`
3. 在 `XxxUser` 类里设 `weight = N`（按业务比例）
4. 想加 SKIP 开关：在 `_common.py` 加 `SKIP_XXX = env_flag("LOCUST_SKIP_XXX")`，
   `XxxUser` 里 `weight = 0 if SKIP_XXX else N`

### 11.3 升级 Locust 版本

```bash
python -m pip install --upgrade locust
```

注意：本压测用的是 `weight = 0` 跳过 User 类的方式（Locust 2.x 起支持）。
低版本可能不识别，需 `pip install "locust>=2.31"`。

### 11.4 提交压测结果到团队

报告产物在 `stress_tests/.reports/`。**默认 .gitignore 不忽略这个目录**——
首次提交建议在仓库根 `.gitignore` 加：

```gitignore
stress_tests/.reports/
```

如果想保存某次跑的报告做基线，单独 commit `stress_tests/.reports/baseline_<日期>.html`。

---

## 12. 更多资料

- 故障排查（按错误信息查）：[`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md)
- 测试者勾选清单：[`CHECKLIST.md`](./CHECKLIST.md)
- Locust 官方文档：<https://docs.locust.io/>
- 后端项目主 README：[../README.md](../README.md)
- 后端 API 路由源码：[`app/api/routes.py`](../app/api/routes.py)

