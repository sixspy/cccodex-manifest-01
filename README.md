# cc-manifest-lab

CC_Codex 二期（26.11）**数据工程**产品线：**湖区落地清单台**（Litestar + Preact CDN + SQLite）。

## 一句话骨架（自有，非外仓抄题）

**湖区落地批次 → 对象指纹登记 → 清单闭合 → 可见性对账**

与现有七线正交（不是采集目录、ETL 管道、同步源、解析入库、编排流、种子摄取、留存裁撤）。
不碰外仓常见指纹（Feed 归并 / CDC 游标 / 质检签发 / Slicegate 类仓名）。

## 领域

- **所属领域**：数据工程
- **子方向**：数仓与数据湖（落地清单 / 对象登记闭环）

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Python 3.12、Litestar、Uvicorn、SQLite |
| 前端 | 原生 JS SPA（全中文，侧栏），靛青竖纹 + 亮青强调；不依赖外网 CDN |
| 测试 | pytest + httpx（本地秒级闭环，优先于 compose） |

## 基线已通主链

1. 湖区落地批次列表（收集中 / 已闭合）
2. 向批次登记对象键与内容指纹
3. 闭合清单（对象数固化，拒绝空批）
4. 可见性对账报告（闭合批 vs 开放批 vs 孤儿对象）

## 规则轨（灌库门槛切换）

收集中批的登记准入由**规则轨**统一裁决，三面（批次列表、登记详情、规则卡片）同显现行轨号。

- **命运择一（已选）**：规则轨走**旁路快照表 `rule_tracks` + 整段原子替换现行**，不采用不可变文件物化。
  切换在一个 SQLite 写事务内完成：插入一条全新不可变快照、旧轨置 `is_current=0`，部分唯一索引
  `idx_rule_tracks_current` 保证现行轨恒唯一；历史轨永不改写，全程留痕。
- **轨号与门槛同一判定**：登记接口必须回传页面持有的 `rule_track_no`，服务端在同一 `BEGIN IMMEDIATE`
  事务内读现行轨并对号，轨号不符直接 409 拦回，再按该轨三门槛（最小字节数 / 指纹最小长度 /
  对象键必填目录前缀）裁决，从结构上杜绝“按旧门槛放行却落成新轨”的拧巴。
- **并发串行**：两名值班同时切换在 `BEGIN IMMEDIATE` 写锁上排队，先到者换号、后到者的期望轨号
  失配即失败（409 中文报错），绝不双成功互相覆盖；连接 `timeout=15s` 让锁等待而非立即报错。
- **落盘可验**：规则轨与对象的准入轨号 `landing_objects.rule_track_no` 均落本机 SQLite，
  可直接查表验证；旧库启动时自动补物化初始轨 RULE-0001（旧门槛）并幂等迁移。
- 初始轨 `RULE-0001` 沿用灌库前宽松门槛（字节 ≥0、指纹 ≥1 位、前缀不限）。

预留切口（未做，供后续功能题）：闭合作废回开放、批量失败闭互斥、跨区依赖冻结、清单回灌等。

## 本地（推荐反馈环）

```powershell
cd claude-workspace\cc-manifest-lab
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest
python run.py
```

http://127.0.0.1:6900/login · **admin / 123456**

## Docker

```powershell
docker compose up -d --build
```

端口 **6900**。冒烟：`python ..\scripts\_smoke_lab_access.py --dir .`
