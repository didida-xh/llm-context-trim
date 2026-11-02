"""全量拼接基线：逐轮本地计数，未超窗前真实调用，首次超窗时记录拒绝证据。"""

import argparse
import csv
import json
import os
from pathlib import Path

from src.llm_client import MODEL_PRIMARY, chat
from src.token_counter import real_tokens

ROOT = Path(__file__).resolve().parent.parent
MAX_CONTEXT = int(os.getenv("MAX_CONTEXT", "5000"))
SYSTEM_PROMPT = "你是一个企业内部的流程助手，回答简洁准确，遵守用户设定的规则。"

def load_turns(path):
    return json.loads(path.read_text(encoding="utf-8"))["turns"]

def build_messages(turns,upto):
    """拼第 upto 轮的全量请求；最后一轮 assistant 为空，不加入历史。"""
    messages=[{"role":"system","content":SYSTEM_PROMPT}]
    for t in turns:
        if t["turn"]>upto:
            break
        messages.append({"role":"user","content":t["user"]})
        if t["assistant"]:
            messages.append({"role":"assistant","content":t["assistant"]})
    return messages

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="src", default="data/session_main.json")
    parser.add_argument("--out", default="reports/baseline.csv")
    args = parser.parse_args()

    turns = load_turns(ROOT / args.src)
    rows, first_over, over_probed = [], None, False

    for t in turns:
        messages=build_messages(turns,t["turn"])
        est=real_tokens(messages,model=MODEL_PRIMARY)
        status,real_in,note="skipped","",""

        if est<=MAX_CONTEXT:
            try:
                r=chat(messages,model=MODEL_PRIMARY)
                status="ok"
                real_in=r.input_tokens
            except Exception as e:
                status="error"
                note=f"{type(e).__name__}: {str(e)[:80]}"
        elif not over_probed:
            over_probed=True
            first_over=t["turn"]
            try:
                r=chat(messages,model=MODEL_PRIMARY)
                status="ok_over_estimate"
                real_in=r.input_tokens
            except Exception as e:
                status= "rejected"
                note=f"{type(e).__name__}: {str(e)[:80]}"

        if first_over and t["turn"]>first_over:
            note=note or"已超窗，停止发送"

        rows.append(
            {
                "turn": t["turn"],
                "est_tokens": est,
                "real_prompt_tokens": real_in,
                "over_limit": "yes" if est > MAX_CONTEXT else "no",
                "status": status,
                "note": note,
            }
        )
        print(f"第 {t['turn']:2d} 轮  估算 {est:5d}  真实 {real_in or '-':>5}  {status}")

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n首次超过 {MAX_CONTEXT} 的轮次：{first_over}")
    print(f"结果已写入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()














