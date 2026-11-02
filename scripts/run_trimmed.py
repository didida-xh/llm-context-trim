"""裁剪版：每轮用 context_manager.assemble 组装后真实发送，与基线对比出降幅。"""

import argparse
import csv
import json
from pathlib import Path

from src.context_manager import assemble
from src.llm_client import MODEL_PRIMARY, chat


from src.policy import split_turns
from src.summarizer_redis import summarize, get_summary, save_summary

from src.cost import cost_of
from src.storage import upsert_session, insert_turn, insert_summary, update_session_total,get_session_total

ROOT = Path(__file__).resolve().parent.parent

def load_session(path):
    """读会话脚本，返回整个 dict（turns + session_id 都要）。"""
    return json.loads(path.read_text(encoding="utf-8"))

def load_baseline(path):
    """读基线 CSV → {turn: est_tokens}，用于逐轮算降幅。"""
    base = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            base[int(row["turn"])] = int(row["est_tokens"])
    return base

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="src", default="data/session_main.json")
    parser.add_argument("--baseline", default="reports/baseline.csv")
    parser.add_argument("--out", default="reports/trimmed.csv")
    args = parser.parse_args()

    data = load_session(ROOT / args.src)
    turns = data["turns"]
    sid = data["session_id"]
    baseline = load_baseline(ROOT / args.baseline)
    rows = []

    summary, offset = "", 0  # ← 状态机初始化（rows = [] 附近）
    summary_tokens = 0
    cache_hits = 0

    db_sid = f"{sid}_trimmed"  # 落库用专属名字，和将来 baseline 落库不冲突
    upsert_session(db_sid, MODEL_PRIMARY)
    total_cost = 0.0

    for t in turns:
        upto=t["turn"]

        _,older= split_turns(turns, upto)
        #只摘"挤出窗口且还没摘过"的轮次；第1轮已 pinned，跳过
        new_turns = [x for x in older if x["turn"] > offset and x["turn"] != 1]
        summary_mode = "none"

        if new_turns:
            new_offset = new_turns[-1]["turn"]
            cached = get_summary(sid, new_offset)
            if cached:
                summary=cached
                summary_mode="cached"
                cache_hits += 1
            else:
                summary, si, so = summarize(summary, new_turns)
                save_summary(sid, new_offset, summary)
                summary_cost = cost_of(MODEL_PRIMARY, si, so)
                insert_summary(db_sid, new_offset, summary, si + so, summary_cost)
                total_cost += summary_cost
                summary_tokens += si + so
                summary_mode = "fresh"
            offset = new_offset

        messages,dropped,breakdown=assemble(turns,upto,MODEL_PRIMARY, summary=summary)

        status,real_in,real_out,note="skipped","",0,""
        try:
            r=chat(messages,model=MODEL_PRIMARY)
            status="ok"
            real_in=r.input_tokens
            real_out=r.output_tokens
        except Exception as e:
            status="error"
            note = f"{type(e).__name__}: {str(e)[:80]}"

        base_est = baseline.get(upto, 0)
        reduction=(1-breakdown["sent"]/base_est) if base_est else 0

        turn_cost = cost_of(MODEL_PRIMARY, real_in, real_out) if status == "ok" else 0.0
        insert_turn(db_sid, upto, int(breakdown["sent"]), real_out,
                    summary_tokens, turn_cost, reduction)
        total_cost += turn_cost

        rows.append({
            "turn": upto,
            "trimmed_tokens": breakdown["sent"],
            "real_prompt_tokens": real_in,
            "baseline_tokens": base_est,
            "reduction_pct": f"{reduction:.1%}",
            "dropped_count": len(dropped),
            "over_budget": "yes" if breakdown["over_budget"] else "no",
            "status": status,
            "note": note,
            "summary_tokens": summary_tokens,  # 累计值，最后一行就是总账
            "summary_mode": summary_mode,  # none / cached / fresh
        })
        print(f"第 {upto:2d} 轮  裁剪 {breakdown['sent']:5d}  基线 {base_est:5d}  降 {reduction:6.1%}  {status}")

#把rows数据写进文件
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    last = rows[-1]
    tail = [r for r in rows if r["turn"] >= 20]
    avg_red = sum(float(r["reduction_pct"].rstrip("%")) for r in tail) / len(tail)
    print(f"\n第 30 轮降幅：{last['reduction_pct']}（裁剪 {last['trimmed_tokens']} vs 基线 {last['baseline_tokens']}）")
    print(f"第 20~30 轮平均降幅：{avg_red:.1f}%  （验收线 ≥40%）")
    print(f"超预算轮次：{sum(1 for r in rows if r['over_budget'] == 'yes')}（要求 0）")
    print(f"结果已写入 {out.relative_to(ROOT)}")
    print(f"摘要总成本：{summary_tokens} tokens，缓存命中 {cache_hits} 次")
    print(f"（提示：再跑一遍，cache_hits 应该变成全部命中——这就是幂等）")
    update_session_total(db_sid, total_cost)
    print(f"本场实验总成本：${total_cost:.6f}")

    final_total = get_session_total(db_sid)
    update_session_total(db_sid, final_total)
    print(f"本次运行花费：${total_cost:.6f}")  # 运行口径：这一遍掏了多少钱
    print(f"账本累计总成本：${final_total:.6f}")  # 账本口径：这场会话终身总支出
if __name__ == "__main__":
    main()







