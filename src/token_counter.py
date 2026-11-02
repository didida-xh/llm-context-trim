import json
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parent.parent

_ENCODING = tiktoken.get_encoding("cl100k_base")

FACTORS = {
    "deepseek": 0.56,
    "qwen": 0.612,
    "default": 0.55,
}

def provider_of(model: str) -> str:
    """从模型名推断厂商，用于选系数。"""
    m = model.lower()
    if "deepseek" in m:
        return "deepseek"
    if "qwen" in m or "dashscope" in m:
        return "qwen"
    return "default"

def count_text(text:str)-> int:
    """一段文本的估算 token 数。"""
    if not text:
        return 0
    return len(_ENCODING.encode(text))

def count_messages(messages,tools=None)-> int:
    """整个请求的估算 token 数。"""
    total=3
    for m in messages:
        total+=4
        total+=count_text(m.get("role",""))
        total+=count_text(m.get("content")or"")
        # 工具调用消息里的结构化内容也要数
        for call in m.get("tool_calls")or []:
            total+=count_text(json.dumps(call,ensure_ascii=False))
    # 工具定义计算
    if tools:
        total+=count_text(json.dumps(tools,ensure_ascii=False))
    return total

def factor_for(model:str)-> float:
    return FACTORS[provider_of(model)]

def real_tokens(messages,tools=None,model="deepseek/deepseek-chat")->int:
    est=count_messages(messages,tools)
    return int(est*factor_for(model))





























