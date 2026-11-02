"""滚动摘要：把被挤出窗口的历史压缩成短文，增量更新而非全量重摘。

核心保证：数字、金额、专有名词、否定表述原样保留——摘要最常见的死法就是丢这些。
"""

from src.llm_client import chat, MODEL_PRIMARY

SUMMARY_PROMPT = """你是一个会话压缩器。把【旧摘要】和【新对话】合并成一段不超过300字的新摘要。
规则：
1. 数字、金额、专有名词、否定表述必须原样保留，禁止改写或省略
2. 旧摘要仍成立的内容保留；被新对话推翻的，以新对话为准
3. 只输出摘要正文，不要任何解释、前缀或标题
"""

def turns_to_text(turns):
    """轮次列表 → 纯文本对话稿，喂给摘要模型。"""
    lines=[]
    for t in turns:
        lines.append(f"用户:{t['user']}")
        if t["assistant"]:
            lines.append(f"助手:{t['assistant']}")
    return "\n".join(lines)

def summarize(old_summary, older_turns,model=MODEL_PRIMARY):
    """增量摘要：new = LLM(old_summary + 新挤出的轮次)。

       返回 (新摘要文本, 摘要自身消耗的 input_tokens, output_tokens)。
       摘要成本必须单独记账——不计入它，降本数字就是虚的。
    """
    if not older_turns:
        return old_summary or "",0,0

    user_msg=(
        f"【旧摘要】\n{old_summary or '（无，这是首次摘要）'}\n\n"
        f"【新对话】\n{turns_to_text(older_turns)}"
    )

    messages = [
        {"role": "system", "content": SUMMARY_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    r=chat(messages, model=model)
    return r.text.strip(), r.input_tokens, r.output_tokens



"""摘要缓存：Redis 按 offset 存摘要，重跑不重复调 API。

key 设计：summary:{session_id}:{offset}
  - offset = 已并入摘要的最后一轮编号
  - 同一会话不同进度对应不同摘要，互不覆盖
幂等：同一 offset 先查缓存，命中就不再调摘要模型
"""

import os
from pathlib import Path

import redis
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SUMMARY_TTL = 7 * 24 * 3600  # 摘要留一周，实验周期内过期不了，单位为s

def summary_key(session_id,offset):
    return f"summary:{session_id}:{offset}"

def get_summary(session_id,offset):
    """查缓存。命中返回 str，未命中返回 None。"""
    r = redis.from_url(REDIS_URL)
    val = r.get(summary_key(session_id, offset))
    return val.decode("utf-8") if val else None

def save_summary(session_id,offset,text):
    """写缓存。ex=TTL 到期自动删，幂等——同 offset 重写即覆盖。"""
    r = redis.from_url(REDIS_URL)
    r.set(summary_key(session_id,offset),text,ex=SUMMARY_TTL)











