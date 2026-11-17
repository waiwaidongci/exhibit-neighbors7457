-- SQLAlchemy metadata 在应用启动时幂等建表；本文件记录嵌套留一研究的物理结构。
-- 嵌套留一选参研究与原 recommendation_runs 研究互不影响，原研究完整保留。
CREATE TABLE IF NOT EXISTS nested_loocv_studies (
  id INTEGER PRIMARY KEY AUTO_INCREMENT,
  source_id INTEGER NOT NULL,
  name VARCHAR(160) NOT NULL,
  grid JSON NOT NULL COMMENT '页面指定的候选邻居数与共同评分门槛',
  snapshot JSON NOT NULL COMMENT '冻结来源、样本与外层留出计划',
  state VARCHAR(20) NOT NULL DEFAULT 'queued' COMMENT 'queued/running/completed/failed',
  result JSON NULL COMMENT '全部外层折完成后统一发布的嵌套结果',
  error TEXT,
  created_at VARCHAR(40) NOT NULL,
  completed_at VARCHAR(40) NULL,
  CONSTRAINT fk_nested_study_source FOREIGN KEY (source_id) REFERENCES exhibitions(id)
);
CREATE TABLE IF NOT EXISTS nested_loocv_folds (
  id INTEGER PRIMARY KEY AUTO_INCREMENT,
  study_id INTEGER NOT NULL,
  fold_index INTEGER NOT NULL,
  state VARCHAR(20) NOT NULL DEFAULT 'queued' COMMENT 'queued/running/completed/failed',
  result JSON NULL COMMENT '逐折依据：内层各候选RMSE、选参、唯一一次外层评估',
  error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0 COMMENT '实际接手次数，同次重试不重复计入',
  worker_id VARCHAR(80) NULL,
  lease_token VARCHAR(80) NULL,
  heartbeat_at VARCHAR(40) NULL,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  UNIQUE KEY ix_nested_fold_study_index (study_id, fold_index),
  CONSTRAINT fk_nested_fold_study FOREIGN KEY (study_id) REFERENCES nested_loocv_studies(id)
);
