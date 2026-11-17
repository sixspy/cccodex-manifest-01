import os
import sqlite3
import threading

import pytest

from app.config import Config
from app.services.objects import ServiceError, register_object
from app.services.rules import get_current_rule, list_rules, switch_rule

STRICT = {
    "min_byte_size": 64,
    "min_hash_length": 8,
    "required_key_prefix": "raw/",
    "remark": "收紧：目录灌库门槛",
}


def open_batch(authed):
    items = authed.get("/api/batches", params={"status": "收集中"}).json()["items"]
    return items[0]["id"]


def test_current_rule_seeded_and_visible_on_three_surfaces(authed):
    meta = authed.get("/api/meta").json()["current_rule"]
    assert meta["track_code"] == "RULE-0001"
    assert meta["is_current"] == 1

    batches = authed.get("/api/batches").json()
    assert batches["current_rule"]["track_no"] == 1

    bid = open_batch(authed)
    detail = authed.get(f"/api/batches/{bid}").json()
    assert detail["current_rule"]["track_code"] == "RULE-0001"

    tracks = authed.get("/api/rule-tracks").json()
    assert tracks["current"]["track_no"] == 1
    assert len(tracks["items"]) == 1


def test_switch_is_atomic_and_rule_card_reads_new_track(authed):
    r = authed.post(
        "/api/rule-tracks/switch",
        json={**STRICT, "expected_track_no": 1},
    )
    assert r.status_code == 200
    new_rule = r.json()["rule"]
    assert new_rule["track_code"] == "RULE-0002"
    assert new_rule["is_current"] == 1

    tracks = authed.get("/api/rule-tracks").json()
    assert tracks["current"]["track_no"] == 2
    # 旧轨不可变留痕，已退位
    old = next(t for t in tracks["items"] if t["track_no"] == 1)
    assert old["is_current"] == 0
    assert old["min_byte_size"] == 0
    current_rows = [t for t in tracks["items"] if t["is_current"] == 1]
    assert len(current_rows) == 1

    # 批次列表、登记详情两面同步读到新轨号
    assert authed.get("/api/batches").json()["current_rule"]["track_no"] == 2
    bid = open_batch(authed)
    assert authed.get(f"/api/batches/{bid}").json()["current_rule"]["track_no"] == 2


def test_register_rejects_stale_track_number(authed):
    bid = open_batch(authed)
    authed.post("/api/rule-tracks/switch", json={**STRICT, "expected_track_no": 1})

    # 页面仍持旧轨号 1 → 同一判定对不上，拦回且中文报错
    r = authed.post(
        f"/api/batches/{bid}/objects",
        json={
            "object_key": "raw/a.parquet",
            "content_hash": "0123456789",
            "byte_size": 100,
            "rule_track_no": 1,
        },
    )
    assert r.status_code == 409
    assert "RULE-0001" in r.json()["error"]
    assert "RULE-0002" in r.json()["error"]

    # 缺轨号同样被拒
    r_missing = authed.post(
        f"/api/batches/{bid}/objects",
        json={"object_key": "raw/a.parquet", "content_hash": "0123456789", "byte_size": 100},
    )
    assert r_missing.status_code == 400


def test_threshold_enforced_after_switch_and_admission_recorded(authed):
    bid = open_batch(authed)
    authed.post("/api/rule-tracks/switch", json={**STRICT, "expected_track_no": 1})

    ok = authed.post(
        f"/api/batches/{bid}/objects",
        json={
            "object_key": "raw/ok.parquet",
            "content_hash": "0123456789",
            "byte_size": 100,
            "rule_track_no": 2,
        },
    )
    assert ok.status_code == 200
    assert ok.json()["object"]["rule_track_no"] == 2

    small = authed.post(
        f"/api/batches/{bid}/objects",
        json={
            "object_key": "raw/small.parquet",
            "content_hash": "0123456789",
            "byte_size": 10,
            "rule_track_no": 2,
        },
    )
    assert small.status_code == 422
    assert "最小字节数" in small.json()["error"]

    bad_prefix = authed.post(
        f"/api/batches/{bid}/objects",
        json={
            "object_key": "tmp/wrong.parquet",
            "content_hash": "0123456789",
            "byte_size": 100,
            "rule_track_no": 2,
        },
    )
    assert bad_prefix.status_code == 422
    assert "目录前缀" in bad_prefix.json()["error"]

    short_hash = authed.post(
        f"/api/batches/{bid}/objects",
        json={
            "object_key": "raw/x.parquet",
            "content_hash": "abc",
            "byte_size": 100,
            "rule_track_no": 2,
        },
    )
    assert short_hash.status_code == 422
    assert "指纹" in short_hash.json()["error"]


def test_switch_guard_and_validation_errors_are_chinese(authed):
    # 期望轨号对不上 → 409，不产生新轨
    r = authed.post(
        "/api/rule-tracks/switch",
        json={**STRICT, "expected_track_no": 99},
    )
    assert r.status_code == 409
    assert "轨号已变化" in r.json()["error"]
    assert authed.get("/api/rule-tracks").json()["current"]["track_no"] == 1

    # 非法门槛 → 400 中文，现行轨不动
    bad = authed.post(
        "/api/rule-tracks/switch",
        json={
            "min_byte_size": -1,
            "min_hash_length": 8,
            "required_key_prefix": "raw/",
            "expected_track_no": 1,
        },
    )
    assert bad.status_code == 400
    assert "不能为负" in bad.json()["error"]

    # 与现行轨完全一致 → 拒绝空切
    same = authed.post(
        "/api/rule-tracks/switch",
        json={
            "min_byte_size": 0,
            "min_hash_length": 1,
            "required_key_prefix": "",
            "expected_track_no": 1,
        },
    )
    assert same.status_code == 400
    assert "无需切换" in same.json()["error"]
    assert authed.get("/api/rule-tracks").json()["current"]["track_no"] == 1


def test_concurrent_switches_serialize_no_double_success_overwrite(tmp_path):
    db = tmp_path / "concurrent.db"
    os.environ["DATABASE_PATH"] = str(db)
    from app.db import init_db

    init_db(str(db))
    barrier = threading.Barrier(2)
    errors: list[Exception] = []
    results: list[dict] = []

    def duty(min_bytes: int) -> None:
        try:
            barrier.wait()
            payload = {
                "min_byte_size": min_bytes,
                "min_hash_length": 8,
                "required_key_prefix": "raw/",
                "remark": f"值班{min_bytes}",
            }
            results.append(switch_rule(payload, expected_track_no=1, db_path=str(db)))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=duty, args=(64,))
    t2 = threading.Thread(target=duty, args=(128,))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    # 恰好一成一败：败者期望轨号 1 已被先到者顶掉，绝无互相覆盖
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ServiceError)
    assert errors[0].status_code == 409

    rules = list_rules(db_path=str(db))
    assert len(rules) == 2
    current = get_current_rule(db_path=str(db))
    assert current["track_no"] == 2
    assert current["min_byte_size"] in (64, 128)
    # 现行轨唯一
    assert sum(1 for r in rules if r["is_current"] == 1) == 1


def test_concurrent_register_and_switch_never_split_judgement(tmp_path):
    db = tmp_path / "concurrent_reg.db"
    os.environ["DATABASE_PATH"] = str(db)
    from app.db import init_db

    init_db(str(db))
    from app.services.batches import list_batches

    bid = list_batches(db_path=str(db))[0]["id"]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def register() -> None:
        barrier.wait()
        try:
            register_object(
                bid, "raw/a.parquet", "0123456789", 100,
                rule_track_no=1, db_path=str(db),
            )
            outcomes.append("registered")
        except ServiceError as exc:
            outcomes.append(f"blocked:{exc.status_code}")

    def switch() -> None:
        barrier.wait()
        try:
            switch_rule({**STRICT, "expected_track_no": 1}, db_path=str(db))
            outcomes.append("switched")
        except ServiceError as exc:
            outcomes.append(f"blocked:{exc.status_code}")

    t1 = threading.Thread(target=register)
    t2 = threading.Thread(target=switch)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    # 写锁串行后只有两种合法结局，绝不出现“按旧门槛放行却落成新轨”的拧巴
    assert set(outcomes) in (
        {"registered", "switched"},
        {"blocked:409", "switched"},
    )


def test_rule_track_persisted_on_disk(tmp_path):
    db = tmp_path / "persist.db"
    os.environ["DATABASE_PATH"] = str(db)
    from app.db import init_db

    init_db(str(db))
    switch_rule({**STRICT, "expected_track_no": 1}, db_path=str(db))

    # 重新以独立连接打开落盘库验证：任务可验
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM rule_tracks ORDER BY track_no").fetchall()
    assert len(rows) == 2
    current = conn.execute("SELECT * FROM rule_tracks WHERE is_current=1").fetchone()
    assert current["track_code"] == "RULE-0002"
    assert current["required_key_prefix"] == "raw/"
    conn.close()


def test_old_database_gets_seed_track(tmp_path):
    # 模拟无规则轨表内容的旧库初始化后可平滑补轨
    db = tmp_path / "old.db"
    os.environ["DATABASE_PATH"] = str(db)
    from app.db import init_db

    init_db(str(db))
    rule = get_current_rule(db_path=str(db))
    assert rule["track_code"] == "RULE-0001"
    assert rule["min_byte_size"] == 0


def test_rule_endpoints_require_login(client):
    assert client.get("/api/rule-tracks").status_code == 401
    r = client.post("/api/rule-tracks/switch", json=STRICT)
    assert r.status_code == 401
    # 未登录拿不到现行轨号
    assert client.get("/api/meta").status_code == 401


@pytest.fixture(autouse=True)
def _restore_db_path():
    yield
    os.environ["DATABASE_PATH"] = Config.DEFAULT_DB
