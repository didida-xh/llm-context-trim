"""成本报表：从 PostgreSQL 按 session_id 导出累计成本 CSV。

账本（数据库）与报表（CSV）分工：数据库可查可聚合，CSV 是导出快照。
"""

import csv
from pathlib import Path

from src.storage import get_conn

ROOT = Path(__file__).resolve().parent.parent

def query_session(db_sid):
    """查一场会话的逐轮明细（主链路 + 摘要费用并成每轮一行）。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT turn, input_tokens, output_tokens, summary_tokens,
                      cost, reduction_pct
               FROM turns
               WHERE session_id = %s
               ORDER BY turn""",
            (db_sid,),
        )
        return cur.fetchall()

def query_summaries(db_sid):
    """查这场会话的摘要账：offset → 那次摘要的 tokens 和 cost。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT summarized_offset, tokens, cost
               FROM summaries
               WHERE session_id = %s
               ORDER BY summarized_offset""",
            (db_sid,),
        )
        return cur.fetchall()

def query_baseline(path):
    """读基线 CSV → {turn: (est_tokens, real_prompt_tokens)}。

    est_tokens 是降幅分母（估算口径），real_prompt_tokens 是成本分母（实测口径）。
    """
    base = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            base[int(row["turn"])] = (int(row["est_tokens"]),
                                      int(row["real_prompt_tokens"] or 0))
    return base

def build_comparison(base, turn_rows, summaries, input_price):
    """拼对比表：token 列用估算口径，成本列用实测口径，两列各自自洽。

    基线崩掉的轮次（real=0）标超窗：成本计 None，累计冻结，不拿 0 冒充。
    """
    rows = []
    base_cum, trim_cum = 0.0, 0.0
    for turn, tin, tout, stokens, cost, red in turn_rows:
        est, real = base.get(turn, (0, 0))

        # 全量侧：实测输入 × 输入单价（输出两侧相当，不计，声明口径即可）
        if real > 0:
            base_cost = real / 1e6 * input_price
            base_cum += base_cost
        else:
            base_cost = None

        # 裁剪侧：账本真实成本 + 归属到本轮的摘要钱
        summary_cost = summaries.get(turn - 8, 0.0)
        trim_cost = float(cost) + summary_cost
        trim_cum += trim_cost

        rows.append({
            "turn": turn,
            "base_tokens": est,
            "trimmed_tokens": tin,
            "reduction_pct": f"{float(red):.1%}" if red is not None else "",
            "base_cost": "超窗" if base_cost is None else f"{base_cost:.6f}",
            "base_cum": f"{base_cum:.6f}",
            "trim_cost": f"{trim_cost:.6f}",
            "trim_cum": f"{trim_cum:.6f}",
            "saving": "—" if base_cost is None else f"{base_cum - trim_cum:.6f}",
        })
    return rows, base_cum, trim_cum

def export_csv(db_sid, out_path):
    """把一场会话导出成验收 CSV：每轮一行 + cumulative_cost 累计列。"""
    summaries = {off: float(cost) for off, _tok, cost in query_summaries(db_sid)}

    rows = []
    cumulative = 0.0
    for turn, tin, tout, stokens, cost, red in query_session(db_sid):
        # offset=o 的摘要在第 o+8 轮生成（RECENT_TURNS=8），费用记到那轮头上
        summary_cost = summaries.get(turn - 8, 0.0)
        cumulative += float(cost) + summary_cost
        rows.append({
            "session_id": db_sid,
            "turn": turn,
            "input_tokens": tin,
            "output_tokens": tout,
            "summary_tokens": stokens,
            "cost": f"{float(cost) + summary_cost:.8f}",
            "cumulative_cost": f"{cumulative:.8f}",
            "reduction_pct": f"{float(red):.1%}" if red is not None else "",
        })

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"导出 {len(rows)} 行 → {out_path}")
    print(f"本场累计成本: ${cumulative:.6f}")
    return cumulative

def render_html(rows, base_cum, trim_cum, trim20_cum, out_path):
    """渲染 HTML 看板：上半屏指标卡，下半屏逐轮对比表。"""
    dead = sum(1 for r in rows if r["base_cost"] == "超窗")
    save_rate = 1 - trim20_cum / base_cum if base_cum else 0

    # 三个口径并列，缺一就是误导：
    # token 降幅（验收口径）/ 同段成本节省（真实口径）/ 超窗轮次（可行性对照）
    cards = [
        ("第 30 轮 token 降幅（验收口径）", rows[-1]["reduction_pct"]),
        ("前 20 轮同段成本节省（真实口径）", f"{save_rate:.1%}"),
        ("基线超窗轮次", f"{dead} / {len(rows)}"),
        ("裁剪超窗轮次", f"0 / {len(rows)}"),
    ]
    card_html = "".join(
        f'<div class="card"><div class="num">{v}</div>'
        f'<div class="label">{k}</div></div>'
        for k, v in cards
    )

    trs = []
    for r in rows:
        cls = ' class="dead"' if r["base_cost"] == "超窗" else ""
        trs.append(
            f'<tr{cls}>'
            f'<td>{r["turn"]}</td><td>{r["base_tokens"]}</td>'
            f'<td>{r["trimmed_tokens"]}</td><td>{r["reduction_pct"]}</td>'
            f'<td>{r["base_cost"]}</td><td>{r["base_cum"]}</td>'
            f'<td>{r["trim_cost"]}</td><td>{r["trim_cum"]}</td>'
            f'<td>{r["saving"]}</td></tr>'
        )

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>多轮会话上下文裁剪 · 成本看板</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 32px; color: #1a1a2e; }}
  h1 {{ font-size: 22px; }}
  .cards {{ display: flex; gap: 16px; margin: 20px 0; }}
  .card {{ flex: 1; border: 1px solid #e0e0e0; border-radius: 10px;
          padding: 16px; text-align: center; }}
  .num {{ font-size: 28px; font-weight: 700; color: #0f62fe; }}
  .label {{ font-size: 12px; color: #666; margin-top: 6px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ border: 1px solid #ddd; padding: 5px 8px; text-align: right; }}
  th {{ background: #f4f6f8; }}
  tr.dead td {{ color: #b00; background: #fff5f5; }}
  .note {{ font-size: 12px; color: #888; margin-top: 14px; line-height: 1.7; }}
</style>
</head>
<body>
<h1>多轮会话上下文裁剪 · 成本看板（main_001_trimmed vs 基线）</h1>
<div class="cards">{card_html}</div>
<table>
<tr><th>轮次</th><th>全量 token</th><th>裁剪 token</th><th>降幅</th>
    <th>全量轮成本</th><th>全量累计</th>
    <th>裁剪轮成本</th><th>裁剪累计</th><th>节省</th></tr>
{''.join(trs)}
</table>
<p class="note">
  口径说明：全量侧成本 = 实测输入 token × 输入单价 $0.28/M（输出两侧相当，不计入）；
  裁剪侧为完整口径（实测输入 + 输出 + 分摊的摘要费用）。<br>
  红色行 = 基线超窗失败轮次：该方案自第 21 轮起无法继续，成本累计冻结于第 20 轮。
  "节省"列在超窗行显示 —，因为对方已弃赛。
</p>
</body>
</html>"""

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"看板已生成 → {out_path}")
    print(f"指标卡：降幅 {rows[-1]['reduction_pct']} | 同段节省 {save_rate:.1%} | "
          f"基线超窗 {dead} 轮 vs 裁剪 0 轮")

if __name__ == "__main__":
    export_csv("main_001_trimmed", ROOT / "reports" / "cost_report.csv")
    base = query_baseline(ROOT / "reports" / "baseline.csv")
    print(f"基线 {len(base)} 轮，第 30 轮：{base[30]}")
    hurt = [t for t, (est, real) in base.items() if real == 0]
    print(f"基线未实际调用的轮次：{hurt}")

    turns_rows = query_session("main_001_trimmed")
    summaries = {off: float(c) for off, _tok, c in query_summaries("main_001_trimmed")}
    rows, base_cum, trim_cum = build_comparison(base, turns_rows, summaries, 0.28)
    print(f"末行：{rows[-1]}")
    print(f"全量累计（20 轮后崩）：${base_cum:.6f}")
    print(f"裁剪累计（30 轮全通，含摘要）：${trim_cum:.6f}")
    print(f"裁剪前 20 轮累计：${float(rows[19]['trim_cum']):.6f}")

    render_html(rows, base_cum, trim_cum, float(rows[19]["trim_cum"]),
                ROOT / "reports" / "report.html")