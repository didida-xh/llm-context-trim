"""分层保留策略：定义优先级 + 预算分配。

裁剪不是"从队首开始砍"，而是"按预算从最低优先级开始丢"。
优先级（高→低）：system → tools → pinned → recent N 轮 → summary → 可丢弃
"""

import os
from pathlib import Path
from dotenv import load_dotenv

from src.token_counter import real_tokens

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MAX_CONTEXT = int(os.getenv("MAX_CONTEXT", "5000"))
RECENT_TURNS = int(os.getenv("RECENT_TURNS", "8"))

def split_turns(turns,upto):
    """把第 1~upto 轮切成两段：recent（保留原文）和 older（候选丢弃）。"""
    recent_start=max(1,upto-RECENT_TURNS+1)
    recent=[t for t in turns if recent_start<=t["turn"]<=upto]#满足条件的留下组成新列表。
    older=[t for t in turns if t["turn"]<recent_start]
    return recent,older

def budget_report(system_msgs, pinned_msgs, older_turns, recent_turns, model):
    """算每层 token 占用，返回 dict——这就是"分层预算占用表"的数据源。"""
    def turn_msgs(ts):
        msgs=[]
        for t in ts:
            msgs.append({"role":"user","content":t["user"]})
            if t["assistant"]:
                msgs.append({"role":"assistant","content":t["assistant"]})
        return msgs

    layers = {
        "system": real_tokens(system_msgs, model=model),
        "pinned": real_tokens(pinned_msgs, model=model),
        "recent": real_tokens(turn_msgs(recent_turns), model=model),
        "droppable": real_tokens(turn_msgs(older_turns), model=model),
    }
    layers["total"] = sum(layers.values())
    layers["budget"] = MAX_CONTEXT
    return layers















