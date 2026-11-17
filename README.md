# 苇谱展陈偏好配对册

用户均值中心化相似邻居协同过滤与留一评分RMSE。为苇谱研究协作组的12位分析与业务人员提供从样本核对、独立worker计算到批准快照交付的完整工作流。

## 真实业务场景

匿名试展显式评分→邻居推荐与冷启动说明→策展备选；仅使用训练评分，相似度共同评分阈值。团队原先将 `exhibit-neighbors-202611-01.csv` 复制到多份人工表格，复核时再逐项对照。三个痛点是口径不可追溯、重复计算易错、业务说明与结果脱节。上游提供合成业务观察，系统承担研究和复核，下游业务人员读取批准快照。全部演示数据是固定随机种子生成的模拟数据。

## 业务模块

1. 业务总览：批次、样本、任务与批准交付账册。
2. 试展评分册：创建/修改来源说明、查看样本、用真实业务字段追加记录；列表分页。
3. 分析配置：冻结来源、参数和全部样本，进入Redis队列。
4. 完整结果：真实算法摘要、专属诊断、全部CSV导出和可恢复失败态。
5. 嵌套留一：页面指定候选邻居数与共同评分门槛，嵌套留一选参，外层评分全程隔离。
6. 策展备选：分析师草案→负责人批准→阅读者下载批准快照。
7. 审计：登录、来源修改、任务worker完成、业务说明批准等事件。

## 快速启动

```bash
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
# 可重复执行，不覆盖人工修改
docker compose exec -T backend python -m scripts.seed_demo
```

访问 [前端](http://localhost:18249)；[API文档](http://localhost:19249/docs)。数据库仅演示用途映射 `17749`。首次准备依赖和初始化数据库需要等待健康状态：`docker compose ps`，五个服务均需healthy。

| 组件 | 技术与职责 |
|---|---|
| frontend | 原生 HTML / CSS / JS ES Modules，Nginx代理 `/api/` |
| backend | Python3.11 / FastAPI / SQLAlchemy / JWT |
| worker | 独立Python进程，Redis BLPOP队列，持久化结果/CSV/心跳 |
| db | MySQL8，命名卷持久化 |
| redis | Redis7，AOF命名卷 |

恰好五服务，均配置healthcheck；后端/worker依赖数据库与Redis健康，前端依赖后端健康；worker与后端共享 `artifacts` 命名卷。重置演示环境：`docker compose down -v --remove-orphans`，会删除本项目卷。

## 账号与权限

所有演示账号密码：`Research2026!`。

| 邮箱 | 角色 | 权限 |
|---|---|---|
| admin@exhibit-neighbors.demo | admin | 全部录入/执行、批准业务说明 |
| analyst@exhibit-neighbors.demo | analyst | 录入来源/样本、执行、提交说明，无批准权 |
| viewer@exhibit-neighbors.demo | viewer | 只读、导出结果及已批准快照 |

时间在数据库/API中以UTC存储，总览与审计页面明确转换为Asia/Shanghai北京时间。

JWT有效期8小时；PBKDF2盐化哈希保存密码；角色每请求从数据库读取。访客写操作403，匿名访问业务数据401，重复样本409，非法业务字段422，重复批准409。每客户端/路径写请求90次/分钟，超限429。生产使用需修改JWT及演示数据库凭据。

## 实体与字段

Exhibition / VisitorRating / RecommendationRun / CuratorSelection 是核心实体；另有User和Audit。数据库实际表：`exhibitions` → `visitor_ratings` → `recommendation_runs` → `curator_selections`。样本以固定字段JSON保存，后端按以下字段严格验证，不接受未知字段替代必填值：匿名访客（`visitor`）、展品代号（`exhibit`）、评分 1–5（`rating`）。

实体贯穿 `backend/src/models`、`schemas`、`services`、`controllers`、`routes`，以及 `frontend/src/api/research.js`、`stores/research.js` 和 `pages`。`Source.key` 与 `(Record.source_id, reference)` 为自然键；来源改名不会修改已冻结任务输入。业务说明在创建时保存结果快照，批准后从该快照导出。

## 算法与口径

用户均值中心化相似邻居协同过滤与留一评分RMSE，全部计算位于 `backend/src/services/algorithm.py`，前端不拟合模型、不生成模拟结果。输入是冻结业务观察及参数，输出summary、专属诊断、逐行计算明细、seed与输入SHA256。CSV包含全部结果行；页面仅预览前30行。参数：

- `neighbors`：相似邻居上限，默认 `5`。
- `min_common`：共同评分下限，默认 `2`。

访客均值中心化的余弦相似度，仅使用正相似邻居；每位访客留一评分验证，留出评分不进入训练。无邻居时回退展品均值。

失败态：业务字段缺失/非有限数值、参数越界、样本不足、退化特征或结构错误均明确拒绝或将任务标failed，错误消息保存在任务中。可回到来源或配置修正后重新执行，历史失败记录保留。

已知边界：显式1–5评分，不是隐式行为推荐；按展品代码确定性留一，不能当作时间外推验证。相似度只取正值，至少两个共同评分；回退会显示依据。生产建议在完成评估后用全量已知评分，不将训练评分误报验证。

## 嵌套留一选参研究（07 模块）

原研究在同批评分上手选 `neighbors/min_common` 再报留一 RMSE，选参与评估共用数据，误差偏乐观。嵌套留一研究（`nested_loocv_studies` / `nested_loocv_folds` 两张新表，与原 `recommendation_runs` 互不影响，原研究完整保留）解决该问题：

- **候选参数由页面指定**：候选邻居数（1–20）与共同评分门槛（2–10）以逗号分隔在页面填写，后端 `validate_grid` 校验整数、范围、去重与组合上限（≤100 组）。
- **外层全程隔离**：外层折按原口径为每位至少3评分访客固定留最后一件；该留出评分从训练矩阵剔除，内层始终不可见。
- **内层每折重算**：内层在外层训练集内再做留一，每个候选组合都重新拟合访客均值、相似邻居与展品/全局回退值（复用 `algorithm.py` 的 `fit_ratings/predict`，口径一致），以内层 RMSE 最小者选参，并列时取邻居更少、门槛更低的确定性组合。
- **仅评一次外层**：选定参数后，外层留出评分只预测一次；逐折依据（全部候选的内层 RMSE 与逐次评估、选定参数、唯一一次外层评估）落盘在 `nested_loocv_folds.result`。
- **统一发布**：全部外层折完成的同一事务里才写 `nested_loocv_studies.result` 并置 completed，页面在中途只显示折进度，不产生半成品汇总。完成后可导出逐折外层 CSV 与候选选参 CSV。

并发与可恢复语义（独立 worker 进程，Redis `nested:queue`，可横向扩多个 worker）：

- 候选折以 `study_id:fold_id` 为消息独立投递，多执行者并发消费；折状态分**待算 queued / 计算中 running / 已完成 completed**（失败 failed 另计），页面三态区分并自动刷新。
- **已落盘折不重算**：claim 阶段对 completed 折直接确认消息；中断重启后只有未完成折会被重新投递。
- **租约与接管**：执行者领取折时获得唯一 `lease_token`，计算中每 8 秒续租；心跳超过 30 秒的失联折由任意在线 worker 的孤儿扫描重置并重新投递，新执行者接管（审计 `nested.takeover` / `nested.orphan_requeue`）。
- **旧执行者迟归不覆盖**：完成/失败回报都校验 token 与当前状态，token 不符的迟到结果直接丢弃（审计 `nested.stale_drop`），接管者结果生效。
- **同次重试不重复计入**：`attempts` 只在真正进入 running（初次领取/失败重试/失联接管）时 +1；健康租约下的重复消息直接确认，不计数、不重算。失败折可在页面逐折人工重试（审计 `nested.retry`）。
- 全部折失败时研究置 failed 并保留逐折错误；任一折重试成功并全部完成后仍会统一发布。

## 演示数据与一天剧本

固定seed=42，3个来源 ` WP-202611-01/02/03`，每来源240条，共720条业务记录；三个账号和首轮种子任务。自然键检查后仅插入缺失数据，连跑seed不重复、不覆盖用户修改。`SEED_DEMO=true` 时启动自动执行；失败记录日志但不阻塞服务。首轮任务同样由独立worker真实计算。

1. 09:00 负责人查看三份来源和720条样本。
2. 09:10 分析师选择 `WP-202611-01 秋季常规组`，核对 `exhibit-neighbors-202611-01.csv`。
3. 09:15 在“试展评分册”核对匿名访客（`visitor`）、展品代号（`exhibit`）、评分 1–5（`rating`），追加编号 `WP-NEW-001` 的样本。
4. 09:20 新建“十一月复核研究”，按默认参数冻结241条输入。
5. 09:21 独立worker消费Redis队列，返回实算结果与输入校验和。
6. 09:25 分析师查看专属诊断及逐行结果，并下载CSV复算。
7. 09:30 提交“十一月业务复核意见”，负责人核对并批准。
8. 09:40 viewer下载批准快照，查看审计中的worker完成与批准事件。

## 目录与本地开发

```text
frontend/src/api, stores, types, components/common, hooks, pages, router, utils, constants
backend/src/routes, controllers, services, models, schemas, middlewares, utils, constants, config
backend/scripts/seed_demo.py
backend/migrations/001_initial.sql, 002_nested_loocv.sql
```

嵌套留一相关文件：后端 `models/nested_study.py`、`models/nested_fold.py`、`services/nested_algorithm.py`、`services/nested_studies.py`、`schemas/nested_study.py`、`controllers/nested_studies.py`、`routes/nested_studies.py`；前端 `pages/nested.js` 与 `api/research.js` 的 `nestedApi`。

本地后端：Python3.11虚拟环境安装 `backend/requirements.txt`，设置 `DATABASE_URL`、`REDIS_URL`、`ARTIFACT_DIR`，在backend目录运行 `uvicorn src.main:app --reload`；另开终端运行 `python -m src.worker`。原生前端无需编译，推荐以Compose Nginx访问，保证 `/api/` 代理。前端为Google Fonts双字体、oklch色板，离线字体回退serif；1280/768/390响应布局。

| 环境变量 | 默认与说明 |
|---|---|
| COMPOSE_PROJECT_NAME | `exhibit-neighbors`，隔离容器/网络/命名卷 |
| FRONTEND_PORT / BACKEND_PORT / DB_PORT | `18249 / 19249 / 17749` |
| SEED_DEMO | `true` |
| JWT_SECRET | `.env.example`演示值，部署前修改 |
| DATABASE_URL / REDIS_URL | Compose内部连接，定义在compose的env锚点 |
| ARTIFACT_DIR | `/artifacts`，后端worker共享 |

共享枚举：`backend/src/constants/enums.py` 与 `frontend/src/constants/enums.js` 都定义 admin/analyst/viewer、queued/running/completed/failed、draft/approved。后端service检查对应字符串；README为全量值说明。共享前端组件table/status/chart/business跨总览、结果、配置与复核复用；actions hook和format utils负责交互/格式。后端errors与rate_limit是两个中间件。

测试及截图在批根 `test-artifacts/gb-0045/`，不混入源码。独立算法oracle、HTTP完整流程和真实Playwright浏览器流程见该目录 `TESTING.md`。License：MIT。
