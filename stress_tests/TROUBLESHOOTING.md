# 故障排查手册

按"错误信息 / 现象"找对应章节。处理不掉的发 issue 时请贴上：

1. 完整命令
2. 完整报错（不要只截最后一行）
3. `python --version`、`python -m locust --version`、操作系统
4. 后端是否真起着（`curl http://127.0.0.1:8000/api/ad-intel/agents/state-schema`）

---

## 目录

- [smoke 不通过](#smoke-不通过)
- [Locust 启动相关](#locust-启动相关)
- [运行时报错](#运行时报错)
- [依赖与环境](#依赖与环境)
- [报告与产物](#报告与产物)
- [Windows 特有问题](#windows-特有问题)

---

## smoke 不通过

### 现象 1：`ConnectError: All connection attempts failed`

**原因**：后端没起 / 端口不对。

**处理**：

```bash
# 1. 确认后端进程在
curl http://127.0.0.1:8000/api/ad-intel/agents/state-schema

# Windows
Invoke-WebRequest http://127.0.0.1:8000/api/ad-intel/agents/state-schema
```

如果 connection refused：另开终端起后端，确认终端打印
`Uvicorn running on http://127.0.0.1:8000`，并且**没退出**。

如果你后端用了别的端口，加 `--host`：

```bash
python stress_tests/smoke.py --host http://127.0.0.1:8765
```

### 现象 2：`status=500` 在 `GET /tasks/recent` 上

**原因**：`data/tasks.json` 损坏 / 不存在 / 不是合法 JSON。

**处理**：

```bash
# 删除文件，后端重启时会重建空 JSON
rm data/tasks.json
# Windows
Remove-Item data\tasks.json
```

重启后端再跑 smoke。

### 现象 3：`POST /agents/rag/run` 返回 `503` 或 `500`

**原因**：Agent 4 的 Chroma 持久化目录权限有问题，或 sentence-transformers 加载失败。

**处理**：

```bash
# 看后端 traceback，通常会提示
# Either model file too large / Out of memory / disk full

# 临时跳过 Agent 4
python stress_tests/smoke.py --skip-llm --skip-heavy --host http://127.0.0.1:8000
# Agent 4 在 smoke 里默认会调用，没法用 CLI 单独 skip。
# 解决：暂时改 smoke.py 注释掉 rag 那段，或先把 Chroma 修好
```

更彻底：

```bash
python -m app.services.memory.cli status
# 看是不是初始化失败
```

### 现象 4：`POST /run` 返回 `422`

**原因**：你改过 payload 工厂，或 backend 的 RunRequest 字段变了。

**处理**：

```bash
# 看响应 body 里 detail，告诉你哪个字段不对
curl -X POST http://127.0.0.1:8000/api/ad-intel/run \
  -H "Content-Type: application/json" \
  -d '{"ad_type":"防晒霜","platform":"xhs","limit":1,"max_comments_per_note":1}'
```

如果是 `enable_media_download` / `keywords` 等字段缺失，对比
`stress_tests/scenarios/_common.py::make_run_payload` 与
`app/models/schemas.py::RunRequest` 的字段。

---

## Locust 启动相关

### 现象 1：`No module named 'scenarios'`

**原因**：你不在仓库根目录跑 locust。

**处理**：必须在 `D:\AIAD` / `~/AIAD` 这种**仓库根**跑命令：

```bash
cd /path/to/AIAD       # 先 cd
python -m locust -f stress_tests/locustfile.py ...
```

不要 `cd stress_tests` 再跑——locustfile 内部 `from scenarios.xxx import ...`
是相对仓库根的路径。

### 现象 2：`No module named 'locust'`

**原因**：当前 Python 没装 locust。

**处理**：

```bash
# 确认你在哪个 Python 上
which python                 # Linux / macOS
Get-Command python           # Windows PowerShell

# 装到这个 Python 上
python -m pip install -r stress_tests/requirements.txt
```

如果你用 conda：先 `conda activate aiad` 再装。

### 现象 3：`prompt_toolkit.output.win32.NoConsoleScreenBufferError`

**现象**：在某些自动化 shell（VSCode 集成终端、Cursor shell）跑 pytest / locust 时崩。

**原因**：被 `prompt_toolkit` 误用，要找 Windows 控制台 buffer。

**处理**：

```powershell
# Windows PowerShell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
python -m locust ...
```

或者换成系统自带的 `cmd.exe` / Windows Terminal。

### 现象 4：Web UI 打不开（`localhost:8089` 拒绝连接）

**原因**：你跑了 `--headless` 模式，没有 Web UI。

**处理**：去掉 `--headless`：

```bash
python -m locust -f stress_tests/locustfile.py --host http://127.0.0.1:8000
# 默认就是 UI 模式
```

或者你用了别的 web port：

```bash
python -m locust -f ... --web-port 9090
```

---

## 运行时报错

### 现象 1：大量 `failures: server error 500`

**原因**：后端在压力下挂了，或 LLM API 限流。

**处理**：

1. 看 `_failures.csv` 里 `Error` 字段
2. 看后端日志（uvicorn 的终端输出 / `logs/` 目录）
3. 减少并发数：`--users 5` 试试
4. 如果是 LLM 限流：设 `LOCUST_SKIP_LLM=1` 跳过

### 现象 2：`Locked file: data/tasks.json`

**原因**：多个 worker 同时写 TaskStore，文件锁等待。

**处理**：这其实是压测想暴露的问题之一！记录现象到报告。
临时绕过：用 `LOCUST_SKIP_HEAVY_RUN=1` 跳过提交类。

### 现象 3：所有用户秒退（"All users stopped"）

**原因**：你设了所有 SKIP 开关，导致所有 User 类 weight=0。

**处理**：至少留一个不 SKIP：

```bash
# 错：全 skip
LOCUST_SKIP_LLM=1 LOCUST_SKIP_HEAVY_RUN=1 LOCUST_SKIP_SSE=1 ...
# ↑ 仍然有 ReadOnlyUser、Agent4RagUser、FullJourneyUser 在跑（FullJourney 受 HEAVY 影响）

# 检查：列出当前 active 的 user 类
python -c "
import os; os.environ['LOCUST_SKIP_LLM']='1'
os.environ['LOCUST_SKIP_HEAVY_RUN']='1'
os.environ['LOCUST_SKIP_SSE']='1'
import sys; sys.path.insert(0,'stress_tests')
import locustfile
for cls_name in ['ReadOnlyUser','SubmitOnlyUser','FullJourneyUser','SseListenerUser',
                 'Agent2VisionUser','Agent3ContextUser','Agent4RagUser','Agent5CopywriterUser']:
    cls = getattr(locustfile, cls_name)
    print(f'{cls_name:30s} weight={cls.weight}')
"
```

至少 ReadOnlyUser、Agent4RagUser 应该非 0。

### 现象 4：SSE 用户长时间挂着不退出

**原因**：`LOCUST_SSE_MAX_SECONDS` 太大。

**处理**：

```bash
LOCUST_SSE_MAX_SECONDS=10 python -m locust ...
```

### 现象 5：`StopUser` 在日志里冒红字

**这是已修复问题的旧版本残留**。当前实现用 `weight = 0` 跳过 User 类，
不再 raise StopUser。如果你看到，说明你拉的是旧 commit，重新 pull 即可。

---

## 依赖与环境

### 现象 1：`ImportError: cannot import name 'OpenAI' from 'openai'`

**原因**：openai 包版本太旧（< 1.0）。

**处理**：

```bash
python -m pip install --upgrade "openai>=1.52.0"
```

### 现象 2：`ImportError: ... sentence-transformers`

**原因**：requirements.txt 的 `sentence-transformers>=2.6.0` 没装上（依赖较大）。

**处理**：

```bash
python -m pip install --upgrade "sentence-transformers>=2.6.0"
# 期望最终装上 torch、transformers、tokenizers 等大依赖
```

如果**网络慢**：用国内镜像

```bash
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 现象 3：跑 smoke 后 `ChromaDB` 占用大量内存

**原因**：默认 chroma 行为，每次启动会加载所有 collection。

**处理**：

```bash
# 暂时清空 chroma 目录
rm -rf data/chroma
# Windows
Remove-Item -Recurse -Force data\chroma
```

后端重启会重建空目录。

---

## 报告与产物

### 现象 1：`.reports/` 目录不存在 / 报告没生成

**原因**：你没有用 `--config <conf>` 跑（命令行 mode 不会自动写报告）。

**处理**：

```bash
# 错：UI 模式默认不写文件
python -m locust -f ... --host http://...

# 对：headless + 显式指定 html
python -m locust -f stress_tests/locustfile.py --host http://... \
  --headless --users 5 --spawn-rate 1 --run-time 30s \
  --html stress_tests/.reports/manual.html \
  --csv stress_tests/.reports/manual

# 或者直接用 conf
python -m locust --config stress_tests/conf/smoke.conf
```

### 现象 2：HTML 报告打不开 / 显示乱码

**原因**：报告是 UTF-8，浏览器编码自动识别失败。

**处理**：浏览器右键 → 编码 → UTF-8。或在 VSCode 里打开。

### 现象 3：`_failures.csv` 是空的，但 Locust UI 显示有 failures

**原因**：你看的是中间态，UI 上看到的失败可能是 4xx 而 catch_response 把它标了 success。

**处理**：在 UI 上 STOP 后再看；或看 `_stats.csv` 的 `# fails` 列。

---

## Windows 特有问题

### 现象 1：环境变量设置不生效

**原因**：PowerShell 和 cmd 设环境变量语法不同。

**处理**：

```powershell
# PowerShell
$env:LOCUST_SKIP_LLM = "1"
python -m locust ...

# cmd.exe
set LOCUST_SKIP_LLM=1
python -m locust ...

# Git Bash / WSL
LOCUST_SKIP_LLM=1 python -m locust ...
```

不要在 PowerShell 里写 `LOCUST_SKIP_LLM=1 python ...`（这是 bash 语法，PowerShell 不识别）。

### 现象 2：`UnicodeEncodeError: 'gbk' codec can't encode...`

**原因**：Windows 默认 GBK 编码遇到中文字符崩。

**处理**：

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
python -m locust ...
```

或在 PowerShell 7+ / Windows Terminal 里跑（默认 UTF-8）。

### 现象 3：路径分隔符引发 `FileNotFoundError`

**原因**：用了 `\`（反斜杠）让 Python 解释成转义字符。

**处理**：要么用正斜杠（在 Python 字符串里更安全），要么用 raw string `r"path\to\file"`。
本套代码已统一用 `pathlib.Path`，不会有这问题；但**你写自定义 conf 时**注意。

### 现象 4：conda 环境激活后 `python` 还指向 base

**原因**：conda init 不完整 / shell hook 没生效。

**处理**：用绝对路径调用：

```powershell
D:\conda\envs\aiad\python.exe -m locust ...
```

或者重启终端、重新 `conda init powershell`。

---

## 还是搞不定？

最后一招：把现场数据打包给团队负责人，附上：

1. 仓库当前 commit：`git rev-parse HEAD`
2. 你跑的完整命令（含 env 变量）
3. 后端进程的完整日志（uvicorn 终端输出 + `logs/`）
4. Locust 的完整输出（含报错栈）
5. `python -m pip freeze > pip-freeze.txt` 的输出
6. `python --version`、`uname -a` / `systeminfo`

通常这套现场信息够定位 90% 的问题。
