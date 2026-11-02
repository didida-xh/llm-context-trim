"""组装最终发送的 messages：按优先级装配，超预算时从最低优先级开始丢。

D2 阶段：system/pinned 永不丢，recent 从最旧一端丢，older 全部丢弃。
D3 加入摘要后，older 会先被摘成 summary 再丢。
"""

import json
from pathlib import Path

from src.policy import MAX_CONTEXT, budget_report, split_turns
from src.token_counter import real_tokens

ROOT = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT = "你是一个企业内部的流程助手，回答简洁准确，遵守用户设定的规则。"

def turns_to_messages(turns):
    """轮次列表 → messages；空 assistant 跳过，口径与基线 build_messages 一致。"""
    msgs = []
    for t in turns:
        msgs.append({"role": "user", "content": t["user"]})
        if t["assistant"]:
            msgs.append({"role": "assistant", "content": t["assistant"]})
    return msgs

def assemble(turns,upto,model, summary=""):
    """组装第 upto 轮的请求。summary=已生成的摘要文本，空串表示无摘要。返回 (final_messages, dropped_turns, breakdown)。"""
    recent,older=split_turns(turns,upto)
    # 第 1 轮的硬约束提升为 pinned，从丢弃候选里拎出来
    pinned,older_rest=[],older

    if older and older[0] ["turn"]==1:
        pinned=[{"role":"user","content":older[0]["user"]}]
        older_rest=older[1:]

    system=[{"role":"system","content":SYSTEM_PROMPT}]
    summary_msgs = []
    if summary:
        # 摘要包装成一轮对话：用户告知 + 助手确认，模型读起来像正常历史
        summary_msgs = [
            {"role": "user", "content": f"以下是更早对话的摘要：\n{summary}"},
            {"role": "assistant", "content": "好的，我已了解以上背景。"},
        ]
    messages = system + pinned + summary_msgs + turns_to_messages(recent)

# 预算不够：从 recent 最旧一端开始丢（丢整轮，不截断单条）
    dropped=list(older_rest)
    while  recent and real_tokens(messages,model=model)>MAX_CONTEXT:
        dropped.append(recent.pop(0))
        messages = system + pinned + summary_msgs + turns_to_messages(recent)

    breakdown=budget_report(system,pinned,older_rest,recent,model)
    breakdown["dropped_turns"]=[t["turn"] for t in dropped]
    breakdown["summary"] = real_tokens(summary_msgs, model=model) if summary_msgs else 0
    breakdown["over_budget"] = real_tokens(messages, model=model) > MAX_CONTEXT
    breakdown["sent"] = real_tokens(messages, model=model)
    return messages,dropped,breakdown


