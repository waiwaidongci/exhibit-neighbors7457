# 嵌套留一选参研究测试

对应需求：候选邻居数与共同评分门槛由页面指定；外层留一评分全程隔离，内层每折重算均值、邻居与回退值，以内层误差选参后仅评一次外层并保留逐折依据；候选折支持多执行者并发、中断续跑、失联接管，旧执行者迟归不覆盖接管结果，同次重试不重复计入；全部外层折完成后统一发布；原研究保留。

## 依赖与运行

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt fakeredis httpx
python -I test-artifacts/gb-0045/nested_loocv/test_nested_algorithm.py
python -I test-artifacts/gb-0045/nested_loocv/test_nested_e2e.py
python -I test-artifacts/gb-0045/nested_loocv/test_nested_api.py
```

测试用 SQLite 临时库与 fakeredis，不依赖 docker；数据库与产物目录在系统临时目录下每次新建。

## 覆盖点

### test_nested_algorithm.py（算法口径）

1. 外层折计划与原研究同口径（每位至少 3 评分访客按展品代码固定留最后一件）。
2. 外层隔离：内层评估中同访客的留出展品必不同于外层留出展品；外层留出评分不进入任何内层训练矩阵。
3. 每个候选组合都有独立内层 RMSE 与逐次内层评估，预测值落在 1–5。
4. 选参确实取内层 RMSE 最小者（并列时邻居更少、门槛更低），外层仅评估一次。
5. 同折重算结果完全一致（确定性，中断续跑口径）。
6. 与原研究固定参数普通留一 RMSE 对照输出（乐观口径参照，不参与选参）。
7. `validate_grid` 拒绝空数组、越界、重复、非整数、未知参数键。

### test_nested_e2e.py（编排与并发，7 项）

1. 建研究即逐折落 queued 并向 `nested:queue` 投递每折消息。
2. 3 个 worker 线程并发消费，研究 completed、每折 attempts=1，汇总 RMSE 与逐折平方误差手工复算一致。
3. 中断续跑：清空队列模拟中断，已落盘折保持 completed 且 attempts 不增加，恢复后研究完成。
4. 失联接管：心跳过期 31 秒的折被孤儿扫描重投，新执行者接管并 attempts+1；旧 token 的迟到完成/失败回报均被丢弃（`nested.stale_drop`），接管者结果生效。
5. 失败折人工重试：同次重试只在真正接手时计一次，重复消息对已完成折不重算、不计数。
6. 统一发布：最后一折完成前 `study.result` 始终为空，全部完成的同一事务才发布。
7. 原 `recommendation_runs` 研究走原 `analysis:queue` 全流程正常完成，不受影响。

### test_nested_api.py（HTTP 全流程）

- 匿名 401、viewer 写操作 403；七类非法网格均 422。
- 创建/列表/详情（含逐折）状态码与结构；worker 消费后 completed 且逐折保留全部候选依据。
- 逐折外层 CSV 行数 = 折数+表头；候选选参 CSV 行数 = 折数×候选组合+表头；未完成研究导出 409。
- 重试路由：仅 failed 可重试（否则 409）、viewer 403、未知研究 404。
- 原 `/runs` 路由全流程保持可用。
