"""PostgreSQL 落库：sessions / turns / summaries 三张表。

账本（可查询、可聚合、外键防脏数据）与 CSV（导出快照）分工：
run_trimmed 逐行写入 → report.py 从这里查出数据导出 CSV。
"""

import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://pgadmin@localhost:5433/agentboard")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    total_cost  NUMERIC(12,6) DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS turns (
    id             SERIAL PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES sessions(session_id),
    turn           INT NOT NULL,
    input_tokens   INT NOT NULL,
    output_tokens  INT NOT NULL DEFAULT 0,
    summary_tokens INT NOT NULL DEFAULT 0,
    cost           NUMERIC(10,6) NOT NULL DEFAULT 0,
    reduction_pct  NUMERIC(6,4),
    UNIQUE (session_id, turn)
);

CREATE TABLE IF NOT EXISTS summaries (
    id                 SERIAL PRIMARY KEY,
    session_id         TEXT NOT NULL REFERENCES sessions(session_id),
    summarized_offset  INT NOT NULL,
    content            TEXT NOT NULL,
    tokens             INT NOT NULL DEFAULT 0,
    cost               NUMERIC(10,6) NOT NULL DEFAULT 0,
    UNIQUE (session_id, summarized_offset)
);
"""

def get_conn():
    return psycopg2.connect(POSTGRES_URL)

def init_db():
    """建表。幂等：IF NOT EXISTS，跑多少遍都不会重复建。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)

def upsert_session(session_id, model):
    """登记一场会话。已存在则刷新 model，建档时间保留首次。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO sessions (session_id, model)
               VALUES (%s, %s)
               ON CONFLICT (session_id) DO UPDATE SET model = EXCLUDED.model""",
            (session_id, model),
        )

def insert_turn(session_id, turn, input_tokens, output_tokens=0,
                summary_tokens=0, cost=0.0, reduction_pct=None):
    """写一轮明细账。重跑时同键覆盖。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO turns
                   (session_id, turn, input_tokens, output_tokens,
                    summary_tokens, cost, reduction_pct)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (session_id, turn) DO UPDATE SET
                   input_tokens   = EXCLUDED.input_tokens,
                   output_tokens  = EXCLUDED.output_tokens,
                   summary_tokens = EXCLUDED.summary_tokens,
                   cost           = EXCLUDED.cost,
                   reduction_pct  = EXCLUDED.reduction_pct""",
            (session_id, turn, input_tokens, output_tokens,
             summary_tokens, cost, reduction_pct),
        )

def insert_summary(session_id, summarized_offset, content, tokens=0, cost=0.0):
    """落库一份摘要存档（Redis 的持久备份）。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO summaries
                   (session_id, summarized_offset, content, tokens, cost)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (session_id, summarized_offset) DO UPDATE SET
                   content = EXCLUDED.content,
                   tokens  = EXCLUDED.tokens,
                   cost    = EXCLUDED.cost""",
            (session_id, summarized_offset, content, tokens, cost),
        )

def update_session_total(session_id, total_cost):
    """实验跑完，回填这场会话的总成本。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE sessions SET total_cost = %s WHERE session_id = %s",
            (total_cost, session_id),
        )

def get_session_total(db_sid):
    """从账本聚合总成本：turns + summaries。三账归一的唯一真值。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT COALESCE(SUM(cost), 0) FROM turns     WHERE session_id = %s)
               + (SELECT COALESCE(SUM(cost), 0) FROM summaries WHERE session_id = %s)""",
            (db_sid, db_sid),
        )
        return float(cur.fetchone()[0])

if __name__ == "__main__":
    init_db()
    upsert_session("test_fake", "fake/model")
    insert_turn("test_fake", 1, input_tokens=123, output_tokens=45, cost=0.0001)
    insert_turn("test_fake", 2, input_tokens=456, reduction_pct=12.5)
    insert_summary("test_fake", 1, "这是假摘要内容", tokens=88, cost=0.00002)
    update_session_total("test_fake", 0.00012)
    print("假数据写入完成")