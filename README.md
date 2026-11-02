# 多轮会话上下文裁剪与成本看板

多轮 LLM 会话越聊越贵、最终超窗报错——本项目用**分层保留 + 滚动摘要**治理上下文，并给出一套**双口径成本计量 + PostgreSQL 账本 + HTML 成本看板**来量化降本效果。

**技术栈**：Python · tiktoken · LiteLLM（SDK + Proxy 网关）· Redis · PostgreSQL · HTML/CSS

**文档导航**：[项目总结](项目总结.md)（全景复盘）· [踩坑记录](踩坑记录.md)（16 条坑与教训）· [PLAN.md](PLAN.md)（设计与排期）

## 核心结论（30 轮会话实测）

| 指标 | 数值 | 说明 |
|---|---|---|
| 第 30 轮输入 token 降幅 | **70.2%** | 验收口径：裁剪后 2262 vs 基线估算 7590 |
| 第 20~30 轮平均降幅 | **61.2%** | 验收线 ≥40% |
| 超窗轮次（裁剪版） | **0** | 基线自第 21 轮起超窗失败 10 轮（对照） |
| 摘要关键信息保留率 | **100%** | 110/110 数字关键词命中 |
| 前 20 轮同段成本节省 | **10.5%** | 真实口径：含摘要费用与输出成本后的净节省 |
| 全程账本总成本 | **$0.0240** | 与导出 CSV、sessions 表三账归一 |

> 三个口径并列才能讲清故事：token 省了 70%，钱省 10.5%（摘要前置投入 + 输出不可压缩），
> 但不裁剪的方案第 21 轮就跑不下去了——可行性的差距比钱的差距更大。

## 架构

```
run_trimmed.py（每轮）
  ├─ policy.split_turns        分层：system → pinned → 最近8轮原文 → 摘要 → 丢弃
  ├─ summarizer_redis          增量滚动摘要 + Redis 缓存（key=summary:{sid}:{offset}，TTL 7天）
  ├─ context_manager.assemble  组装 + 预算检查（MAX_CONTEXT=5000）
  ├─ llm_client.chat           发送：直连各厂商，或经 LiteLLM Proxy（.env 的 PROXY_URL 切换）
  ├─ cost.cost_of              双口径算钱：litellm 价目表 vs 自建价目，分歧 >2% 告警
  └─ storage                   落库：sessions / turns / summaries 三表（UPSERT 幂等）
report.py（事后）
  ├─ export_csv                按 session_id 导出累计成本 CSV（验收交付物）
  └─ render_html               成本看板：指标卡 + 基线 vs 裁剪逐轮对比表
litellm/config.yaml（网关，D5）
   模型别名 chat-main/chat-backup + fallbacks + num_retries
docker-compose.yml            等价容器化形态（本机实跑为源码安装 + pip 直跑 proxy）
```

## 快速开始

```bash
# 0) 依赖
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install 'litellm[proxy]'
cp .env.example .env   # 填入 DEEPSEEK_API_KEY 等

# 1) 启动基础服务（重启电脑后需先执行）
~/.local/bin/redis-server --daemonize yes
~/.local/pgsql/bin/pg_ctl -D ~/.local/pgsql/data -o "-p 5433" \
    -l ~/.local/pgsql/data/server.log start
python -m src.storage          # 建表（幂等）

# 2) 跑实验（注意 -m：以项目根为包搜索起点）
python -m scripts.run_baseline     # 基线：全量拼接（重复跑会覆盖同一 CSV）
python -m scripts.run_trimmed      # 裁剪版：摘要 + 落库（第二遍起摘要全走 Redis 缓存）
python -m scripts.eval_retention   # 摘要保留率评测

# 3) 出报表
python -m src.report               # cost_report.csv + report.html

# 4)（可选）网关模式
set -a; source .env; set +a
LITELLM_LOCAL_MODEL_COST_MAP=True litellm --config litellm/config.yaml --port 4000
# .env 里 PROXY_URL=http://localhost:4000 生效即全流量过网关；清空该变量回到直连
```

## 数据溯源（每个数字从哪来）

- **token 数**：模型 usage 实测（估算仅用于发送前预算检查，字段名带 est）
- **单价**：litellm 内置价目表（`model_prices_and_context_window_backup.json`，逐条注明厂商定价页出处），自建 `OUR_PRICES` 交叉校验
- **成本**：本地 单价 × 实测 token，逐笔落库；`sessions.total_cost` 由 turns + summaries 聚合得出（唯一真值）
- **基线成本**：实测输入 × 输入单价 $0.28/M（输出两侧相当不计入，报表脚注声明）

## 已知边界（诚实声明）

- 超窗对照基于人为设定的 MAX_CONTEXT=5000（模拟受限部署），非 DeepSeek 真实 128K 窗口；任何窗口都存在能撑爆它的会话，窗口大小只决定何时爆、不决定是否需要管理
- 未启用 DeepSeek 前缀缓存计费拆分（prompt_cache_hit_tokens），当前记账为保守口径：真实账单只会更低
- 网关延迟 A/B 为小样本均值（+80ms），严格测量需多次采样取 P95
- qwen 校准系数 0.612 未经长会话数据复测

## 踩坑记录

见 [踩坑记录.md](踩坑记录.md)——D1~D6 每天的坑、根因与教训，随做随记。
