# 多轮会话上下文裁剪与成本看板 · 一周实现方案

> 目标：解决"会话越长越贵、最后超窗报错"，并量化降本效果。
> 技术路线：以 LiteLLM 作为统一模型入口（先 SDK，后 Proxy），自己实现上下文裁剪策略层与成本看板。
> 贯穿原则（老师要求）：代码要能跑，但**重点是讲清每个机制背后的原理**。每天结束必须能回答当天的"原理三问"。

---

## 一、验收指标（最终交付口径）

| 指标 | 判定方式 | 通过线 |
|---|---|---|
| 单轮输入 token 降幅 | 第 30 轮单轮 + 第 20~30 轮均值，双口径同时达标 | 均 ≥ 40% |
| 超窗报错次数 | `MAX_CONTEXT=8000` 下逐轮检查请求是否超限 | 0 次 |
| 关键信息保留 | 10 类约束 × 3 次 = 30 次测试，统计第 30 轮正确复述率 | ≥ 80% |
| 成本看板 | 按 `session_id` 导出 CSV，并与内置 cost map 对账 | 文件存在且金额一致（误差 ≤ 2%） |

**关键口径说明**

1. **降幅按"将要发送的 messages 的 token 计数"计算**，不依赖基线真实发出去。基线到后期必然被上游拒绝，用失败请求算不出 token。
2. **必须人为设定 `MAX_CONTEXT=8000`**。若不设，30 轮普通对话远达不到模型真实上限，"超窗 = 0"这条指标就没有对照、没有说服力。同时要记录模型真实上限作为背景数据。
3. **摘要调用自身的成本必须计入总成本**，否则降本数字是虚的。
4. 降幅指标只看后段。前几轮全量 token 本来就小，强行要求每轮都降 40% 不现实。

---

## 二、三个前置决策（已定，无需再讨论）

| 决策 | 取值 | 理由 |
|---|---|---|
| 统一入口 | D1~D4 用 LiteLLM SDK；D5 起切 Proxy | 先用最小代码体会 adapter 抹平了什么，再上工程化 |
| 上下文档位 | 策略上限 `MAX_CONTEXT=8000`，并记录模型真实上限 | 制造可复现的超窗对照 |
| 看板形态 | HTML 报表 + CSV 导出为主，Prometheus 为辅 | Grafana 需额外服务与数据源，成本高；HTML 已满足"可导出"要求 |

其他默认值：
- 主模型 `deepseek/deepseek-chat`（DeepSeek-Flash 系），备用走百炼 OpenAI 兼容口（`openai/qwen-plus` + `api_base`），不依赖特定 provider 命名。
- 摘要模型与主模型同一家（口径一致、成本可查）。
- 保留窗口 `recent N = 8` 轮原文（可调，最终以指标为准）。

---

## 三、目录结构

```
多轮会话上下文裁剪与成本看板/
├── .env / .env.example          # 密钥，.gitignore 必须排除
├── .gitignore
├── requirements.txt
├── docker-compose.yml           # litellm-proxy + redis + postgres
├── litellm/
│   └── config.yaml              # 模型别名 / fallbacks / 预算与限流
├── src/
│   ├── llm_client.py            # litellm.completion 封装（SDK / Proxy 可切换）
│   ├── token_counter.py         # tiktoken 计数 + 与真实 usage 校准
│   ├── policy.py                # 分层保留策略（优先级 + 预算分配）
│   ├── summarizer.py            # 滚动摘要 + Redis 缓存（offset 增量）
│   ├── context_manager.py       # 组装 messages：窗口 + 摘要 + 策略
│   ├── cost.py                  # 成本回填（litellm cost map）+ 交叉校验
│   ├── storage.py               # PostgreSQL 落库
│   └── report.py                # HTML 报表 + CSV 导出
├── scripts/
│   ├── make_long_session.py     # 构造 30 轮会话（第 1 轮埋硬约束）
│   ├── run_baseline.py          # 全量拼接基线
│   ├── run_trimmed.py           # 裁剪版
│   ├── eval_retention.py        # 10 场景 × 3 次信息保留测试
│   ├── load_test.py             # 打满 RPM/TPM 验证 429
│   └── verify_all.py            # 一键跑完出四指标
├── data/                        # 会话脚本、原始 usage 记录
├── reports/                     # report.html、cost_export.csv、对比表
└── README.md                    # 架构、四个数字、无效尝试、踩坑记录
```

---

## 四、七天计划

### D1 · 基线先行：先把"会超窗"证明出来

- [ ] 建 venv，安装 `litellm tiktoken redis psycopg2-binary pandas`
- [ ] `.env` 写入密钥，并按已有项目规矩设置 `NO_PROXY`（含 `api.deepseek.com`、`dashscope.aliyuncs.com`）
- [ ] `llm_client.py` 用 `litellm.completion()` 跑通 DeepSeek + 百炼两家，只改 `model` 字符串
- [ ] 记录两家在请求体、usage 字段、流式 chunk 结构上的差异（写成对比笔记）
- [ ] `token_counter.py`：`tiktoken.get_encoding("cl100k_base")` 估算 + `stream_options={"include_usage": True}` 拿真实值，算校准系数
- [ ] `make_long_session.py`：第 1 轮埋硬约束（金额一律人民币 / 每条回答不超过 3 个要点 / 不得提及竞品 X），第 2~29 轮每轮 200~400 字撑大窗口，第 30 轮追问约束
- [ ] `run_baseline.py`：全量拼接跑 30 轮，输出每轮 input token 曲线，标出在 8000 下第一次越线的轮次

**今日产物**：token 曲线 CSV + 越线轮次结论。
**原理三问**：
1. 为什么 tiktoken 只能做相对比较、不能做绝对判窗？
2. LiteLLM 的 adapter 到底"兼容"了什么，没兼容什么？
3. 上游不返回 usage 时，兜底策略该怎么设计才不让统计静默失真？

### D2 · 滑动窗口 + 分层保留策略

- [ ] `policy.py` 定义优先级：`system` → `tools schema` → `pinned`（用户长期约束）→ `recent N 轮原文` → `summary` → 可丢弃
- [ ] 裁剪实现为"按预算从最低优先级开始丢"，而不是原地 `while` 砍队首
- [ ] `context_manager.py` 输出 `(final_messages, dropped_turns, token_budget_breakdown)`，每层占用可打印
- [ ] `run_trimmed.py` 跑纯窗口版（此阶段不含摘要），算第 30 轮降幅

**今日产物**：窗口版降幅数据 + 分层预算占用表。
**注意**：此时降幅会很好看，但信息一定丢——这是 D3 要补的。
**原理三问**：
1. 分层策略与"保留最近 N 轮"在信息保留上的本质差别在哪？
2. `tools schema` 为什么必须单独占预算、不能计入 messages 的 token 统计？
3. 预算不够时，丢整轮还是截断单条？两种做法的失败模式分别是什么？

### D3 · 滚动摘要 + Redis 缓存（最容易翻车的一天）

- [ ] `summarizer_redis.py` 实现 **增量**摘要：`new_summary = LLM(old_summary + 新挤出的几轮)`，禁止每次对全量历史重摘
- [ ] Redis key：`summary:{session_id}:{summarized_offset}`，offset = 已并入摘要的最后一条消息序号（与 nanobot `consolidated_offset` 同思路）
- [ ] 设置 TTL + 写入幂等，重启后不重复摘要
- [ ] 摘要调用 token 与成本单独记账，并入总成本
- [ ] `eval_retention.py`：10 类约束（数字精度 / 否定词 / 专有名词 / 格式 / 金额 / 时间 / 人物 / 单位 / 语言 / 禁止事项）各跑 3 次，统计复述正确率
- [ ] 若低于 80%，迭代摘要提示词：强制原样保留数字、否定语气、专有名词

**今日产物**：降幅（含摘要成本）与保留率两个数字同屏。
**原理三问**：
1. 增量摘要相对全量重摘，省的是什么成本？代价是什么？
2. 摘要为什么最常吃掉否定词和数字？如何用提示词约束抑制？
3. offset 不持久化会发生什么？

### D4 · 成本计量 + PostgreSQL + CSV

- [ ] `cost.py` 用 `litellm.completion_cost(completion_response=...)` 回填；拿不到完整响应时用 `cost_per_token(model=..., prompt_tokens=..., completion_tokens=...)`
- [ ] 交叉校验：自建价目手算 vs LiteLLM 返回值，误差 ≤ 2% 才算通过
- [ ] `storage.py` 建三表：`sessions` / `turns`（input/output/summary token、cost、reduction）/ `summaries`（offset、内容、成本）
- [ ] `report.py` 导出 CSV：`session_id, turn, model, input_tokens, output_tokens, summary_tokens, cost, cumulative_cost`
- [ ] 阅读 `litellm/cost_calculator.py` 与 `model_prices_and_context_window.json`，确认未知模型的处理方式

**今日产物**：单会话完整成本明细 CSV。
**原理三问**：
1. 成本 map 的价目从哪来、多久更新一次？缓存价与输出价怎么区分？
2. 未知模型为什么必须告警而不是静默按 0 计？
3. 为什么"累计成本"要以 session 为聚合维度而不是以请求？

### D5 · LiteLLM Proxy 上位（fallbacks + 预算 + 限流）

- [ ] `docker-compose.yml`：`ghcr.io/berriai/litellm:main-latest` + redis + postgres（**建议 D4 晚上先 pull 镜像**）
- [ ] `config.yaml` 配 3 个模型别名：`chat-main` / `chat-backup` / `chat-strong`
- [ ] `router_settings` 配 `fallbacks`、`num_retries: 1`（重试交给 fallback，避免叠加长尾）
- [ ] **故障演练**：故意写错 `chat-main` 的 key，验证自动落到 `chat-backup` 并留下 fallback 日志
- [ ] `/key/generate` 建虚拟 key，带 `max_budget` / `rpm_limit` / `tpm_limit`，Redis 做计数后端
- [ ] `load_test.py` 打满配额，验证超限返回 **429**、预算耗尽后拒绝而非静默降级
- [ ] 把 `llm_client.py` 后端切到 Proxy（`api_base=http://localhost:4000`），业务代码零改动

**今日产物**：fallback 切换日志 + 429 证据 + 预算耗尽日志。
**原理三问**：
1. `max_budget` 与 RPM/TPM 的拦截点分别在链路哪一环？
2. 权重路由、最小时延路由、fallbacks 的判定顺序是什么，冲突时谁优先？
3. 虚拟 key 与真实上游 key 的映射关系，在多租户下如何隔离计量？

### D6 · 可观测 + 出对比表

- [ ] Proxy 开 `prometheus` callback，暴露 `/metrics`；可选接 Langfuse 看单次 trace
- [ ] `report.py` 出 `report.html`：上半屏四组指标（QPS / TTFT P95 / 错误率 / 累计成本），下半屏对比表
- [ ] 对比表列：轮次 / 全量 input token / 裁剪后 input token / 降幅 / 全量累计成本 / 裁剪累计成本（含摘要）/ 节省
- [ ] 用 LiteLLM 内置 cost map 复核自建成本脚本，出对账结论

**今日产物**：`reports/report.html` + `reports/cost_export.csv` + 对比表。
**原理三问**：
1. TTFT 应该在客户端计时还是服务端埋点？两者差值来自哪里？
2. Prometheus 的 histogram 分位数如何计算 P95？
3. 回调（callback）机制在 LiteLLM 里挂在哪一层，失败是否影响主链路？

### D7 · 一键验证 + 收口

- [ ] `verify_all.py` 串起全流程，输出四项指标结论
- [ ] `README.md` 写清：架构图、策略优先级、四个数字、无效尝试、踩坑记录
- [ ] 整理"老师可能会问的原理点"自测清单（见第六节）
- [ ] 如需，把关键结论做成汇报提纲（问题 → 方案 → 数据 → 结论 → 决策请求）

**今日产物**：可直接提交的 README + 四个指标盖章结果。

---

## 五、源码阅读与实现映射

| 阅读目标 | 对应实现 | 要带走的东西 |
|---|---|---|
| `litellm/main.py`（`completion()` / `embedding()`） | D1 `llm_client.py` | 参数规范化、重试与 fallback 如何串联 |
| `litellm/llm_providers/`（挑两家 adapter） | D1 双厂商对比笔记 | "兼容"到底兼容了什么 |
| token 计数相关实现 | D1 `token_counter.py` | 流式下 usage 的取得方式 |
| `litellm/cost_calculator.py` + cost map | D4 `cost.py` | 计价规则、未知模型处理 |
| `litellm/proxy/`（路由、鉴权、预算） | D5 proxy 配置 | 预算与限流的拦截位置 |
| `litellm/router.py` | D5 fallbacks 演练 | 权重/最小时延/fallbacks 判定顺序 |
| `litellm/integrations/`（Langfuse / OTel / Prometheus） | D6 回调 | 回调范式与异步边界 |

---

## 六、汇报自测：八个必须答得出的原理点

1. tiktoken 与厂商 tokenizer 的偏差来源，为什么用校准系数而不是硬信一方。
2. LiteLLM adapter 层兼容的是请求字段名、多模态拼装、响应解析、错误码归一，而不是模型能力。
3. retry 与 fallback 的边界：前者解瞬时抖动，后者解供应商级故障；两者叠加会放大长尾。
4. `max_budget` 是累计额度、RPM/TPM 是瞬时速率，拦截点不同。
5. 滚动摘要的 offset 必须持久化，否则重启后重复摘要 → 成本翻倍。
6. 摘要成本计入后，节省率会下降几个百分点——这才是真实降本曲线。
7. 分层保留策略的本质是"优先级 + 预算分配"，不是"保留最近 N 轮"。
8. cost map 只覆盖已知模型，未知模型必须人工价目 + 告警，否则统计静默失真。

---

## 七、风险与规避

| 风险 | 表现 | 规避 |
|---|---|---|
| macOS 上 Docker 跑 proxy | 拉镜像慢、端口冲突 | D4 晚先 pull，端口避开 8000/5432 默认占用 |
| D3 保留率上不去 | 否定词、数字丢失 | 不硬调参到深夜；记录失败类别，D7 补，汇报时如实说瓶颈 |
| 摘要成本被漏算 | 降幅虚高 | 摘要调用单独记账，报表中单列 `summary_tokens` |
| 基线超窗无对照 | 指标空转 | 固定 `MAX_CONTEXT=8000`，并输出越线轮次 |
| 代理干扰高并发 | 连接失败、超时 | `.env` 设 `NO_PROXY`，与项目一保持一致 |

---

## 八、每日打钩

- [ ] D1 基线 + 越线证明
- [ ] D2 窗口 + 分层策略
- [ ] D3 滚动摘要 + Redis + 保留率
- [ ] D4 成本计量 + PG + CSV
- [ ] D5 Proxy + fallbacks + 预算限流
- [ ] D6 指标看板 + 对比表
- [ ] D7 一键验证 + README
