from app.db import get_db


class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def list_objects(batch_id: int, db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM landing_objects WHERE batch_id=? ORDER BY id",
            (batch_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def register_object(
    batch_id: int,
    object_key: str,
    content_hash: str,
    byte_size: int,
    rule_track_no: int | None = None,
    db_path: str | None = None,
) -> dict:
    key = (object_key or "").strip()
    digest = (content_hash or "").strip()
    if not key or not digest:
        raise ServiceError("对象键与内容指纹不能为空")
    if byte_size < 0:
        raise ServiceError("字节数不能为负")
    if not isinstance(rule_track_no, int) or isinstance(rule_track_no, bool):
        raise ServiceError("缺少规则轨号，请刷新页面后按现行轨重新登记", 400)
    with get_db(db_path) as conn:
        # 与切换共用同一把写锁：切换中途不会读到半套规则，轨号与门槛恒同一判定
        conn.execute("BEGIN IMMEDIATE")
        rule = conn.execute(
            "SELECT * FROM rule_tracks WHERE is_current=1"
        ).fetchone()
        if not rule:
            raise ServiceError("现行规则轨缺失，无法登记", 500)
        if rule_track_no != rule["track_no"]:
            raise ServiceError(
                f"规则轨号对不上：页面持有 RULE-{rule_track_no:04d}，"
                f"现行轨为 {rule['track_code']}，登记已拦回，请刷新后重试",
                409,
            )
        if byte_size < rule["min_byte_size"]:
            raise ServiceError(
                f"未达现行轨 {rule['track_code']} 门槛：字节数 {byte_size} "
                f"小于最小字节数 {rule['min_byte_size']}",
                422,
            )
        if len(digest) < rule["min_hash_length"]:
            raise ServiceError(
                f"未达现行轨 {rule['track_code']} 门槛：指纹长度 {len(digest)} "
                f"小于最小指纹长度 {rule['min_hash_length']}",
                422,
            )
        prefix = rule["required_key_prefix"]
        if prefix and not key.startswith(prefix):
            raise ServiceError(
                f"未达现行轨 {rule['track_code']} 门槛：对象键必须以目录前缀 "
                f"「{prefix}」开头",
                422,
            )
        batch = conn.execute(
            "SELECT * FROM landing_batches WHERE id=?", (batch_id,)
        ).fetchone()
        if not batch:
            raise ServiceError("批次不存在", 404)
        if batch["status"] != "收集中":
            raise ServiceError("仅收集中批次可登记对象", 409)
        exists = conn.execute(
            "SELECT id FROM landing_objects WHERE batch_id=? AND object_key=?",
            (batch_id, key),
        ).fetchone()
        if exists:
            raise ServiceError("同批对象键已存在", 409)
        cur = conn.execute(
            "INSERT INTO landing_objects(batch_id, object_key, content_hash, "
            "byte_size, status, rule_track_no) VALUES (?,?,?,?, '已登记', ?)",
            (batch_id, key, digest, int(byte_size), rule["track_no"]),
        )
        row = conn.execute(
            "SELECT * FROM landing_objects WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)
