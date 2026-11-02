"""生成固定的 30 轮测试会话，纯本地构造，不调用模型。"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONSTRAINTS = [
    "所有金额一律使用人民币，并保留两位小数",
    "每条回答不超过 3 个要点",
    "不得提及竞品「星云办公」",
]

CHECK_QUESTION = "我第一轮定的三条规则是什么？请逐条复述，不要遗漏。"

# 12 个业务场景，循环使用，保证内容不重复但长度稳定
TOPICS = [
    ("差旅报销", "出差住宿的报销标准", "一线城市每晚不超过 600.00 元"),
    ("采购审批", "办公用品的采购流程", "单笔超过 2000.00 元需要部门负责人审批"),
    ("合同用印", "合同盖章需要哪些材料", "合同金额超过 50000.00 元需法务复核"),
    ("考勤异常", "忘记打卡怎么补", "每月补卡不超过 3 次"),
    ("培训申请", "外部培训的申请条件", "培训费用在 3000.00 元以内由主管审批"),
    ("资产领用", "笔记本电脑的领用规则", "领用后 3 个工作日内完成登记"),
    ("会议室预定", "会议室预定冲突怎么处理", "提前 15 分钟未到场自动释放"),
    ("客户接待", "客户来访的接待标准", "人均餐费不超过 200.00 元"),
    ("发票开具", "增值税专用发票的开具条件", "需提供完整的开票信息与合同编号"),
    ("权限申请", "系统权限的申请流程", "临时权限有效期不超过 30 天"),
    ("加班调休", "加班时长如何折算调休", "按 1:1 折算，当季度内使用"),
    ("文档归档", "项目文档的归档要求", "项目结项后 10 个工作日内提交"),
]

FILLER = (
    "另外想确认一下这块在系统里的具体入口在哪里，我在门户里只找到了旧的流程说明，"
    "页面上写的审批层级和最新通知似乎不太一致。我们部门上个月刚做过一次组织调整，"
    "原来的二级审批现在可能是三级，我不确定是否需要重新提交历史单据。"
    "如果方便的话，也希望能说明一下常见的退回原因，避免我们反复提交。"
)

def build_user(turn: int) -> str:
    """构造第 turn 轮的用户提问，长度控制在 200~400 字。"""
    name, question, rule = TOPICS[(turn - 2) % len(TOPICS)]
    # 每 3 轮插入一次干扰：金额和竞品名，用来测试约束是否被真正保留
    extra = ""
    if turn % 3 == 0:
        extra = "顺便问一下，如果找「星云办公」这类外部供应商报价，金额是不是按美元结算？"
    elif turn % 3 == 1:
        extra = "上次同事说这笔费用可以走 1234.5 元的标准，我不太确定这个数字对不对。"
    return (
        f"我想咨询一下{name}相关的问题：{question}具体是怎么规定的？"
        f"我们组最近有几位同事都遇到了类似情况，处理方式却不一样，"
        f"所以想确认一下统一的执行口径。{rule}，这个标准现在还有效吗？"
        f"{extra}{FILLER}"
    )

def build_assistant(turn: int) -> str:
    """构造助手的固定回复，长度 300~500 字，不调用模型。"""
    name, _, rule = TOPICS[(turn - 2) % len(TOPICS)]
    return (
        f"关于{name}，目前的执行口径如下：\n"
        f"1. {rule}，该标准自本年度起执行，暂无调整通知；\n"
        f"2. 申请入口在门户的流程中心，选择对应类别后按提示逐级提交，"
        f"审批层级以系统当前配置为准，历史单据不需要重新提交；\n"
        f"3. 常见退回原因包括附件不完整、金额与发票不一致、审批人选择错误。\n"
        f"如果仍有疑问，可以把具体单号发我，我帮你核对一下当前所处的审批节点。"
    )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=30)
    parser.add_argument("--out", default="data/session_main.json")
    args = parser.parse_args()

    turns = []
    for turn in range(1, args.turns + 1):
        if turn == 1:
            user = (
                "在开始之前，请先记住三条规则，后面所有回答都要遵守：\n"
                "1. 所有金额一律使用人民币，并保留两位小数；\n"
                "2. 每条回答不超过 3 个要点；\n"
                "3. 不得提及竞品「星云办公」。\n"
                "确认一下你能做到吗？"
            )
            assistant = (
                "好的，我已记录这三条规则：\n"
                "1. 金额一律使用人民币并保留两位小数；\n"
                "2. 每条回答不超过 3 个要点；\n"
                "3. 不提及竞品「星云办公」。\n"
                "后续回答都会遵守。"
            )
        elif turn == args.turns:
            user = CHECK_QUESTION
            assistant = ""  # 这个问题留给模型回答，用来测保留率
        else:
            user, assistant = build_user(turn), build_assistant(turn)

        turns.append({"turn": turn, "user": user, "assistant": assistant})

    data = {
        "session_id": "main_001",
        "constraints": CONSTRAINTS,
        "check_question": CHECK_QUESTION,
        "turns": turns,
    }

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    lens = [len(t["user"]) + len(t["assistant"]) for t in turns]
    print(f"已写入 {out.relative_to(ROOT)}")
    print(f"轮数 {len(turns)}，每轮字符数 {min(lens)}~{max(lens)}，总计 {sum(lens)} 字")

if __name__ == "__main__":
    main()