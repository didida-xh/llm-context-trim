"""成本计算：LiteLLM 价目表 + 自建价目手算，双口径交叉校验（≤2%）。

数据溯源：模型 usage 实测 token → 单价换算 → NUMERIC 落库。
价目来源：LiteLLM 内置 cost map（本地副本），手工价目来自官方定价页。
"""

from litellm import cost_per_token, completion_cost

# 自建价目表：美元 / 百万 token（经 litellm 探价 + 官网核对，2026-11-02）
OUR_PRICES = {
    "deepseek/deepseek-chat": {"input": 0.28, "output": 0.42},
}

def litellm_cost(model,input_tokens,output_tokens):
    """LiteLLM 口径：价目表自动查。"""
    prompt_cost, completion_cost_ = cost_per_token(
        model=model, prompt_tokens=input_tokens, completion_tokens=output_tokens
    )
    return prompt_cost + completion_cost_

def manual_cost(model, input_tokens, output_tokens):
    """手算口径：自己维护的价目表。未知模型返回 None（调用方决定告警）。"""
    price = OUR_PRICES.get(model)
    if price is None:
        return None
    return input_tokens / 1e6 * price["input"] + output_tokens / 1e6 * price["output"]

def cost_of(model, input_tokens, output_tokens):
    """对外入口：以 LiteLLM 为准，手算超差就告警但不拦账。"""
    cost=litellm_cost(model, input_tokens, output_tokens)
    ref=manual_cost(model, input_tokens, output_tokens)

    if ref is not None and cost>0:
        diff=abs(cost-ref)/ref
        if diff>0.02:
            print(f"⚠️ 成本口径分歧 {diff:.1%}: {model} "
                  f"litellm={cost:.8f} 手算={ref:.8f}")
    return cost

if __name__ == "__main__":
    # 用第 30 轮的真实 token 数对账
    m = "deepseek/deepseek-chat"
    cost = cost_of(m, 2247, 102)
    print(f"litellm 口径总成本: ${cost:.8f}")
    print(f"手算口径: ${manual_cost(m, 2247, 102):.8f}")
    # 故意对账一个未知模型，验证 None 分支
    print(f"未知模型手算: {manual_cost('fake/model', 100, 100)}")