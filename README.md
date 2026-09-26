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
5. **BlendTicket（脂液拼配单）**：单头记 `blendDate`（拼配日）、`targetGrade`（目标品级）、`plannedTotalKg`（计划总重）、`openedBy`（开单人）；`closedAt`（结案时刻）初始留空，结案时连同 `closedBy`（结案人）一起写入
6. **BlendTicketLine（拼配明细）**：挂单头与来脂批、`countedKg`（计入千克）

**业务规则**：将灶台相位切到 `drawing`（出胶）时，进行中的 CookRun 必须至少有一条 SoftPointProbe 的 `softPointC ≤ 95`。逻辑在 `apps/kiln/services/floor_rules.py`，由相位切换入口调用。

**拼配规则**（`apps/kiln/services/blend_rules.py`；开单、结案、开灶挂批三处核验同源，都以「未结案拼配单明细行」为唯一事实来源）：

- **开单**：计入千克合计必须**精确等于**计划总重；单批计入不得大于该批到货千克；同一来脂批已在其他未结案单里的，不得再进本单。
- **结案前（未结案）**：明细内来脂批处于**拼配锁定** —— 不得再进另一张未结案单，也**禁止挂灶开新值守**；来脂批卡片标「拼配锁定」，开灶表单下拉里同样标注并会被服务端拒绝。
- **结案后**：仅**主管**（staff）可结案；结案时按开单同一套规则复核明细，通过后写入结案时刻。锁定随即解除 —— 这些批才允许挂灶开值守、也可再进新拼配单；卡片改标「拼配已结案 · 可开灶」。

## 界面

- 首页：**灶台值守看板** — 左侧班次条 + 按过道排布的灶台瓦片；点瓦片打开右侧抽屉（值守、探针时间线、改相位 / 登记探针 / 开灶）
- 次页：**来脂批** — 卡片时间线，非宽表 CRUD；卡片标注拼配锁定 / 已结案状态
- 三页：**脂液拼配**（班次条「拼」入口）— 开拼配单（单头 + 明细行）、未结案单锁定中、主管一键结案解锁

## 种子数据

```bash
python manage.py seed_data
```

幂等：已有灶台则只保证账号存在。样例地名仅用「松脂坳 / 桐油坑」系。来脂批共五批：前三批已挂值守，**末两批（2410D / 2410E）不挂任何值守与拼配单，可直接开拼配单演示**（如 1250.00 + 760.00 = 计划总重 2010.00 kg）。

## 目录结构

```
PitchKiln-01/
  manage.py
  requirements.txt
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  config/
  apps/kiln/          # 模型、视图、floor_rules、blend_rules、种子、测试
  templates/floor/    # 值守看板 + 抽屉
  templates/resin/    # 来脂批时间线
  templates/blend/    # 脂液拼配单看板
  static/css/         # 值守台 ops-console 样式
```
