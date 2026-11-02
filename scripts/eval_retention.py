"""关键信息保留率评测：验证摘要是否守住了被裁剪轮次里的数字类信息。

从"被摘要覆盖的轮次"（第 2~offset 轮）抽取所有数字关键词，
逐个检查是否原样出现在最终摘要里。通过率 = 命中数 / 总数，验收线 ≥80%。
"""

import json
import re
from pathlib import Path

import redis

from src.summarizer_redis import REDIS_URL, summary_key

ROOT = Path(__file__).resolve().parent.parent

# 数字类关键词：整数/小数，可带 % ％ 元 万 天 等后缀
NUM_RE = re.compile(r"\d+(?:\.\d+)?(?:%|％|元|万|天|个月|个|人|次|年|月|日|点|号)?")

def main():
    session = json.loads((ROOT / "data" / "session_main.json").read_text(encoding="utf-8"))
    turns = session["turns"]
    sid = session["session_id"]

    # 从 Redis 找该会话最新存档（offset 最大的 key），取摘要文本
    r = redis.from_url(REDIS_URL)
    keys = [k.decode("utf-8") for k in r.keys(f"summary:{sid}:*")]
    offset=max(int(k.rsplit(":",1)[-1])for k in keys)
    summary=r.get(summary_key(sid,offset)).decode("utf-8")

    # 被摘要覆盖的轮次：第 2~offset 轮（第 1 轮是 pinned，原文保留，不测）
    covered = [t for t in turns if 2 <= t["turn"] <= offset]
    total, hits, misses = 0, 0, []
    for t in covered:
        for field in ("user", "assistant"):
            for kw in NUM_RE.findall(t[field]):
                total += 1
                if kw in summary:
                    hits += 1
                else:
                    misses.append((t["turn"], field, kw))

    rate = hits / total if total else 0
    print(f"存档点 offset={offset}，测第 2~{offset} 轮，摘要 {len(summary)} 字")
    print(f"关键信息 {total} 个，命中 {hits} 个，通过率 {rate:.1%}（验收线 ≥80%）")
    if misses:
        print("未命中清单：")
        for turn, field, kw in misses:
            print(f"  第{turn}轮 {field}：{kw}")


if __name__ == "__main__":
    main()