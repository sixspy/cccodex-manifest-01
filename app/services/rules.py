from app.db import get_db
from app.services.objects import ServiceError


def track_code(track_no: int) -> str:
    return f"RULE-{track_no:04d}"


def _row(r) -> dict | None:
    return dict(r) if r is not None else None


def get_current_rule(db_path: str | None = None) -> dict:
    """读现行轨。规则轨是登记门槛的唯一真相源；未初始化即视为服务端故障。"""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM rule_tracks WHERE is_current=1"
        ).fetchone()
        if not row:
            raise ServiceError("现行规则轨缺失，请联系管理员初始化规则轨", 500)
        return dict(row)


def list_rules(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM rule_tracks ORDER BY track_no DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def _validate_payload(payload: dict) -> tuple[int, int, str, str]:
    def as_int(name: str, label: str) -> int:
        try:
            return int(payload.get(name))
        except (TypeError, ValueError):
            raise ServiceError(f"{label}必须是整数", 400)

    min_byte_size = as_int("min_byte_size", "最小字节数")
    min_hash_length = as_int("min_hash_length", "指纹最小长度")
    if min_byte_size < 0:
        raise ServiceError("最小字节数不能为负", 400)
    if min_hash_length < 1:
        raise ServiceError("指纹最小长度至少为 1", 400)
    prefix = str(payload.get("required_key_prefix") or "").strip()
    if len(prefix) > 128:
        raise ServiceError("对象键必填目录前缀过长（上限 128 字）", 400)
    remark = str(payload.get("remark") or "").strip()
    if len(remark) > 200:
        raise ServiceError("备注过长（上限 200 字）", 400)
    return min_byte_size, min_hash_length, prefix, remark


def switch_rule(
    payload: dict,
    expected_track_no: int | None = None,
    db_path: str | None = None,
) -> dict:
    """整段原子替换现行轨：一条新不可变快照 + 旧轨退位，同一写事务内一次落成。

    BEGIN IMMEDIATE 立即取写锁，两名值班同时切换在此串行，先到者改号，
    后到者的期望轨号对不上即被拦，绝不双成功互相覆盖。
    """
    min_byte_size, min_hash_length, prefix, remark = _validate_payload(payload)
    with get_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT * FROM rule_tracks WHERE is_current=1"
        ).fetchone()
        if not current:
            raise ServiceError("现行规则轨缺失，无法切换", 500)
        if expected_track_no is not None and expected_track_no != current["track_no"]:
            raise ServiceError(
                f"轨号已变化：您持有的是 RULE-{expected_track_no:04d}，"
                f"现行轨为 {current['track_code']}，请刷新规则卡片后再切换",
                409,
            )
        same = (
            current["min_byte_size"] == min_byte_size
            and current["min_hash_length"] == min_hash_length
            and current["required_key_prefix"] == prefix
        )
        if same:
            raise ServiceError("新门槛与现行轨完全一致，无需切换", 400)
        next_no = conn.execute(
            "SELECT COALESCE(MAX(track_no), 0) + 1 AS n FROM rule_tracks"
        ).fetchone()["n"]
        conn.execute(
            "UPDATE rule_tracks SET is_current=0 WHERE track_no=?",
            (current["track_no"],),
        )
        conn.execute(
            "INSERT INTO rule_tracks(track_no, track_code, min_byte_size, "
            "min_hash_length, required_key_prefix, remark, is_current) "
            "VALUES (?,?,?,?,?,?,1)",
            (next_no, track_code(next_no), min_byte_size, min_hash_length,
             prefix, remark),
        )
        row = conn.execute(
            "SELECT * FROM rule_tracks WHERE track_no=?", (next_no,)
        ).fetchone()
        return dict(row)
