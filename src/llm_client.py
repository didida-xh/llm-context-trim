import os
import time
from dataclasses import dataclass
from pathlib import Path

import litellm
from dotenv import load_dotenv
from litellm import completion

from src.token_counter import count_text, provider_of, real_tokens

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MODEL_PRIMARY = os.getenv("MODEL_PRIMARY", "deepseek/deepseek-chat")
MODEL_BACKUP = os.getenv("MODEL_BACKUP", "qwen-plus")
BASE_BACKUP = os.getenv("DASHSCOPE_BASE_URL")
KEY_BACKUP = os.getenv("DASHSCOPE_API_KEY")

PROXY_URL = os.getenv("PROXY_URL", "")  # 空 = 直连；填了 = 全部流量过网关
PROXY_ALIASES = {  # 真实模型名 → 网关别名
    "deepseek/deepseek-chat": "chat-main",
    "qwen-plus": "chat-backup",
}

TIMEOUT = 30
MAX_RETRIES = 0  # 重试交给上层降级策略，客户端不叠加

if not os.getenv("DEEPSEEK_API_KEY"):
    raise RuntimeError("DEEPSEEK_API_KEY 未配置，主模型入口不可用（检查 .env 或 shell 环境变量）")


@dataclass
class ChatResult:
    text: str
    input_tokens: int
    output_tokens: int
    usage_source: str  # "usage" | "estimate"
    latency_ms: int
    model: str


def _provider_kwargs(model: str) -> dict:
    """把模型名翻译成该厂商的连接参数。

    DeepSeek 走环境变量（DEEPSEEK_API_KEY），百炼走 OpenAI 兼容口。
    切 Proxy 时，这里改成 api_base=PROXY_URL + 虚拟 key 即可。
    """
    if PROXY_URL:
        # 网关模式：openai/ 前缀让 SDK 走 OpenAI 兼容协议，发往 api_base（网关）
        # SDK 发送前会剥掉前缀，网关收到的就是裸别名 chat-main
        return {"model": f"openai/{PROXY_ALIASES.get(model, model)}",
                "api_base": PROXY_URL, "api_key": "local-proxy"}
      
    if provider_of(model) == "qwen":
        # 备用入口按需校验：不配只影响 fallback，不该阻断 DeepSeek 单跑
        if not (BASE_BACKUP and KEY_BACKUP):
            raise RuntimeError(
                "百炼入口缺少 DASHSCOPE_BASE_URL 或 DASHSCOPE_API_KEY，fallback 不可用"
            )
        prefixed = model if model.startswith("openai/") else f"openai/{model}"
        return {"model": prefixed, "api_base": BASE_BACKUP, "api_key": KEY_BACKUP}
    return {"model": model}

def _options(tools, stream: bool) -> dict:
    opts = {"timeout": TIMEOUT, "max_retries": MAX_RETRIES}
    if tools:
        opts["tools"] = tools
    if stream:
        opts["stream"] = True
        # 只有打开它，流式响应才会在最后补一个带 usage 的 chunk
        opts["stream_options"] = {"include_usage": True}
    return opts

def chat(messages,model:str=None,tools=None,stream:bool=False)->ChatResult:
    """发一次请求，返回文本与 token 用量。"""
    model=model or MODEL_PRIMARY
    started=time.perf_counter()

    resp=completion(messages=messages,**_provider_kwargs(model),**_options(tools,stream))

    if stream:
        parts,usage=[],None
        for chunk in resp:
            if chunk.choices:
                content=getattr(chunk.choices[0].delta,"content",None)
                if content:
                    parts.append(content)
            if getattr(chunk,"usage",None):
                    usage=chunk.usage
        text="".join(parts)
    else:
        text=resp.choices[0].message.content or ""
        usage=resp.usage

    latency_ms=int((time.perf_counter() - started) * 1000)

    if usage:
        input_tokens, output_tokens, source = (
            usage.prompt_tokens,
            usage.completion_tokens,
            "usage",
        )
    else:
        # 兜底估算：必须标记来源，否则报告里真假数据会混在一起
        input_tokens = real_tokens(messages, tools, model)
        output_tokens = count_text(text)
        source = "estimate"

    return ChatResult(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        model=model,
        usage_source=source,
    )


if __name__ == "__main__":
    # 自测：真实 usage 与估算换算值应相差 10% 以内
    MESSAGES = [
        {"role": "system", "content": "你是一个简洁的助手，回答不超过两句话。"},
        {"role": "user", "content": "用一句话解释什么是上下文窗口，并说明为什么长会话会超出它。"},
    ]

    for name, mdl in [("DeepSeek", MODEL_PRIMARY), ("百炼", MODEL_BACKUP)]:
        r = chat(MESSAGES, model=mdl)
        est = real_tokens(MESSAGES, model=mdl)
        diff = abs(r.input_tokens - est) / max(est, 1)
        print(f"[{name}] {r.model}  {r.latency_ms}ms")
        print(f"  真实 {r.input_tokens} / 估算换算 {est}  偏差 {diff:.1%}")
        print(f"  输出 {r.output_tokens} tokens  来源 {r.usage_source}")
        print(f"  回复：{r.text[:50]}")















