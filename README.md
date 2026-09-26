# PitchKiln-01 · 灶台值守看板

Django 5 + PostgreSQL：灶台瓦片看板 + 右侧抽屉探针时间线，无 Vue/React SPA。

## 技术栈

- Django 5、PostgreSQL
- Session 登录
- HTMX：局部刷新灶台网格与抽屉
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4710** |
| Postgres | **6110**（容器内 5432） |

数据库账号：`pitchkiln` / `pitchkiln` / 库名 `pitchkiln`

## 快速启动

```bash
cd PitchKiln/PitchKiln-01
docker compose up --build -d
```

浏览器打开：http://localhost:4710

演示账号：

- `admin` / `123456`（超级用户）
- `worker` / `123456`（普通用户）

容器启动时会自动：`migrate` → `seed_data` → `collectstatic` → `gunicorn`

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 确保本机 Postgres 监听 6110，或先 docker compose up -d db
set POSTGRES_HOST=localhost
set POSTGRES_PORT=6110
python manage.py migrate
python manage.py seed_data
python manage.py runserver 0.0.0.0:4710
```

## 业务模型

1. **ResinLot（来脂批）**：`lotCode`、`originPlace`、`arrivalKg`、`receivedAt`
2. **FireHearth（灶台）**：`lane`、`tag`（唯一）、`resinGrade`、相位 `cold|charging|ramping|holding|drawing`
3. **CookRun（熬制值守）**：归属灶台与来脂批、`openedAt`、`closedAt`（可空）、`targetSoftPointC`
4. **SoftPointProbe（软化点探针）**：归属值守、`sampledAt`、`softPointC`、`samplerName`
5. **BlendTicket（脂液拼配单·单头）**：`blendDate`（拼配日）、`targetGrade`（目标品级）、`plannedTotalKg`（计划总重）、`createdBy`（开单人）、`closedAt`（结案时刻，初始留空）
6. **BlendTicketLine（拼配明细）**：挂单头 + `resinLot`（来脂批）+ `countedKg`（计入千克）

**业务规则**：将灶台相位切到 `drawing`（出胶）时，进行中的 CookRun 必须至少有一条 SoftPointProbe 的 `softPointC ≤ 95`。逻辑在 `apps/kiln/services/floor_rules.py`，由相位切换入口调用。

## 脂液拼配规则

逻辑集中在 `apps/kiln/services/blend_rules.py`，**开单核验、结案核验、开灶挂批核验同源**——三处共用同一原语「未结案单（`closedAt` 为空）锁定其明细来脂批」。

**开单校验**（缺一则开单失败）：

- 明细计入千克合计必须**精确等于**计划总重；
- 单批计入不得大于该批到货千克，且必须大于 0；
- 同一来脂批不得在单内重复，也不得已在另一张未结案单里。

**结案前后差异**：

| | 未结案（结案时刻留空） | 主管结案后 |
|---|---|---|
| 明细来脂批 | **拼配锁定**：禁止挂灶开新值守，禁止再进另一张未结案单 | 锁定解除，允许挂灶开值守 |
| 来脂批卡片 | 标「拼配锁定 · 单 #N」徽标 | 徽标消失 |
| 开灶下拉 | 该批标注「（拼配锁定）」，选中即被表单拦截 | 正常可选 |

- 仅主管（超级用户，如 `admin`）可结案；结案时按开单同一套规则复核整单（合计、单批上限、跨单冲突），不过则拒结案。
- 结案核验（`assert_ticket_closeable`）与开灶挂批核验（`assert_lot_free_for_hearth`）同源于 `blend_rules.lot_lock_ticket`。

## 界面

- 首页：**灶台值守看板** — 左侧班次条 + 按过道排布的灶台瓦片；点瓦片打开右侧抽屉（值守、探针时间线、改相位 / 登记探针 / 开灶）
- 次页：**来脂批** — 卡片时间线，非宽表 CRUD；被未结案单锁定的批带「拼配锁定」徽标
- 班次条「拼」：**脂液拼配** — 开拼配单（单头 + 明细行）、单据列表、主管结案入口，附结案前后差异说明

## 种子数据

```bash
python manage.py seed_data
```

幂等：已有灶台则只保证账号与可拼批存在。样例地名仅用「松脂坳 / 桐油坑」系。其中 `脂-松脂坳-2409D`、`脂-桐油坑-2409E` 两批不挂值守、不进拼配单，专供脂液拼配开单演示（可拼）。

## 目录结构

```
PitchKiln-01/
  manage.py
  requirements.txt
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  config/
  apps/kiln/          # 模型、视图、floor_rules、种子
  templates/floor/    # 值守看板 + 抽屉
  templates/resin/    # 来脂批时间线
  static/css/         # 值守台 ops-console 样式
```
