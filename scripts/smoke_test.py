"""D1 冒烟测试：验证 LiteLLM 连通性、流式 usage 是否返回、tiktoken 偏差多大。

跑法（在项目根目录）：
    .venv/bin/python scripts/smoke_test.py

只验证三件事，不涉及任何裁剪逻辑：
1. 两份 provider 能否用同一套代码调通
2. 流式请求能否在最后一个 chunk 拿到 usage
3. tiktoken 的估算值与上游真实 prompt_tokens 的比值
"""

import json
import os
from pathlib import Path

import tiktoken
from dotenv import load_dotenv
from litellm import completion

ROOT = Path(__file__).resolve().parent.parent
# 显式传路径：跨目录运行时 load_dotenv() 默认只找当前工作目录
load_dotenv(ROOT / ".env")

MODEL_PRIMARY = os.getenv("MODEL_PRIMARY", "deepseek/deepseek-chat")
MODEL_BACKUP = os.getenv("MODEL_BACKUP", "qwen-plus")
BASE_BACKUP = os.getenv("DASHSCOPE_BASE_URL")
KEY_BACKUP = os.getenv("DASHSCOPE_API_KEY")

# 两家厂商走同一份 messages，便于对比
MESSAGES = [
    {"role": "system", "content": "你是一个简洁的助手，回答不超过两句话。"},
    {"role": "user", "content": "用一句话解释什么是上下文窗口，并说明为什么长会话会超出它。"},
]

TARGETS = [
    {"name": "DeepSeek", "model": MODEL_PRIMARY},
    {
        "name": "百炼",
        "model": f"openai/{MODEL_BACKUP}",
        "api_base": BASE_BACKUP,
        "api_key": KEY_BACKUP,
    },
]


def estimate_tokens(messages, enc):
    """按 OpenAI 的 chat 格式粗略估算：每条消息固定开销 + 内容 token。"""
    total = 3
    for m in messages:
        total += 4 + len(enc.encode(m["role"])) + len(enc.encode(m["content"]))
    return total


def call(target, stream=False):
    """统一调用入口：除模型名和连接参数外，其余参数完全一致。"""
    kwargs = {
        "model": target["model"],
        "messages": MESSAGES,
        "timeout": 30,
        "max_retries": 0,  # 重试交给上层策略，这里不叠加
    }
    if stream:
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
    if "api_base" in target:
        kwargs["api_base"] = target["api_base"]
        kwargs["api_key"] = target["api_key"]
    return completion(**kwargs)


def test_nonstream(target):
    """验证连通性，并拿到非流式场景的真实 usage。"""
    try:
        resp = call(target)
        usage = resp.usage
        print(f"  [连通] {target['name']} ✓  回复：{resp.choices[0].message.content[:40]}...")
        print(f"  [usage] prompt={usage.prompt_tokens} completion={usage.completion_tokens}")
        return usage.prompt_tokens
    except Exception as e:
        print(f"  [连通] {target['name']} ✗  {type(e).__name__}: {str(e)[:120]}")
        return None


def test_stream_usage(target):
    """验证流式模式最后一个 chunk 是否带 usage。"""
    try:
        stream = call(target, stream=True)
        last_usage, chunk_count, first_keys = None, 0, None
        for chunk in stream:
            chunk_count += 1
            if chunk_count == 1:
                delta = chunk.choices[0].delta if chunk.choices else None
                first_keys = sorted(delta.model_fields_set) if delta else []
            if getattr(chunk, "usage", None):
                last_usage = chunk.usage
        if last_usage:
            print(f"  [usage] {target['name']} 流式 ✓  chunks={chunk_count}  prompt={last_usage.prompt_tokens}")
            return True
        print(f"  [usage] {target['name']} 流式 ✗  未返回 usage（chunks={chunk_count}）")
        return False
    except Exception as e:
        print(f"  [usage] {target['name']} 流式 ✗  {type(e).__name__}: {str(e)[:120]}")
        return False


def main():
    enc = tiktoken.get_encoding("cl100k_base")
    est = estimate_tokens(MESSAGES, enc)
    print(f"tiktoken(cl100k_base) 估算输入 = {est} tokens\n")

    results = {}
    for target in TARGETS:
        print(f"=== {target['name']} ===")
        real = test_nonstream(target)
        stream_ok = test_stream_usage(target)
        if real:
            ratio = round(est / real, 3)
            results[target["name"]] = {
                "model": target["model"],
                "estimated": est,
                "actual_prompt_tokens": real,
                "ratio": ratio,
                "stream_usage": stream_ok,
            }
            print(f"  [校准] 估算/真实 = {est}/{real} = {ratio}")
        print()

    out = ROOT / "data" / "calibration.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"校准结果已写入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
