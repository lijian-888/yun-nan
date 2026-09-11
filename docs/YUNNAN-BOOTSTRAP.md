# 云南省农业科学院原生 PostgreSQL 初始化

## 目标与边界

本仓库把隆耘应用完整结构创建在 `ynaas_rice_ai.public`，与数据库中已有的 `core`、`governance`、`ingest`、`raw`、`ricedata`、`ncbi`、`ai` schema 共存。初始化流程不会删除、改名、迁移或写入这些既有 schema；它会在执行前后读取其中每张表的精确行数并进行一致性校验。

现有 `ricedata.rice_variety`、`ricedata.rice_pedigree_*`、`ricedata.rice_gene*` 与指定 `ncbi.*` 表已通过只读、白名单、固定参数化模板接入智能体。该适配层不改写既有 schema，不接受模型生成的 SQL，并对返回数量和系谱递归深度设置上限。`core.*`、试验事实和治理表仍未映射为隆耘发布数据。

## 安全前提

- 首次执行前必须完成 PostgreSQL 全库备份和 schema 备份。
- 本机 PostgreSQL 需要安装并启用 `vector` 扩展。
- 数据库凭据只保存在本机私密 JSON 文件中；不得提交到 Git 或复制到日志。
- `D:\IDEA-Project\yun-nan\.venv` 必须已安装 `backend` 的三份 requirements。
- 初始化默认设置 `INSTITUTION_DATA_ENABLED=false`，不会连接 MinIO，也不会另建机构数据库。

## 执行

在仓库根目录运行：

```powershell
.\scripts\bootstrap-yunnan.ps1
```

默认凭据文件为：

```text
%APPDATA%\postgresql\ynaas_native_pg_secrets.json
```

可以用 `-SecretPath` 指定同结构的其他私密文件。脚本强制核对数据库名 `ynaas_rice_ai`，设置云南机构边界，并调用 `python -m app.bootstrap_yunnan`。流程可重复执行；每次都重新确认结构、索引、约束、RLS、授权与必要系统配置。

`-IncludeDemoData` 是显式危险开关，会导入旧的演示业务行。云南真实数据库不应使用该参数。

## Keycloak

本地导入模板位于 `keycloak/rice-research-realm.json.example`，生产发布模板位于 `deploy/keycloak/rice-research-realm.json`。二者包含同一个 OIDC Client 和三类互斥业务账号：

- `ynaas.researcher`：科研人员；
- `ynaas.processor`：数据处理员；
- `ynaas.fieldadmin`：字段管理员。

所有初始密码均为占位符或部署变量，并要求首次登录修改。启动 Keycloak 前必须生成独立强密码；不要把替换后的实际导入文件提交到 Git。

Docker Desktop 恢复后，运行 `scripts/start-yunnan-docker.ps1`。该脚本使用 `docker-compose.yunnan.yml` 禁用内部演示 PostgreSQL，连接宿主 `ynaas_rice_ai`，并启动完整本地服务。生成的 Realm、证书和口令均为被 Git 忽略或位于仓库外的运行时文件。

本次实际初始化与验收记录见 [2026-09-11 验证记录](YUNNAN-BOOTSTRAP-VALIDATION-20260911.md)。

## 拉取远端更新的准入条件

只有同时满足以下条件，才可以把远端新代码用于云南智能体本地运行：

1. `docker version` 能返回 Linux Server 版本，当前六项服务的基线验收已通过；
2. 当前分支是云南集成分支且工作区干净；云南改造尚未合并前使用 `codex/yunnan-bootstrap`，合并后才能改从 `main` 更新；
3. `ynaas_rice_ai` 已生成本次更新前的全库备份和 schema 备份；
4. 已审查远端数据库迁移，确认它只做兼容、可重复的增量变更，不会删除、改名或重写既有非 `public` schema；
5. 先执行 `git fetch origin` 检查提交，再执行 `git pull --ff-only`；出现分叉、冲突或本地改动时立即停止，不做强制覆盖；
6. 更新后执行 `scripts/start-yunnan-docker.ps1`，让安全初始化、镜像重建和六项服务启动完整跑完；
7. API、Web、MinIO、MinerU、Keycloak 与 Worker 全部正常，且既有 80 张非 `public` 表的逐表行数与更新前一致、演示业务表仍为 0 行。
8. `YUNNAN_API_KEY` 仅保存在仓库外，CherryIn `agent/deepseek-v4-flash` 真实调用通过，且品种/系谱与基因工具均能以 `ynaas_longyun_api` 最小权限角色返回受控结果。

被 Git 忽略的 Realm、证书和仓库外运行口令是本机状态，正常 `git pull --ff-only` 不会覆盖；如果远端变更了 Realm 模板或认证配置，应在备份当前 Keycloak 数据后重新生成并单独验收账号与角色。

上述条件满足后即可拉取代码并进行本地运行；但“服务已启动”和“智能体能返回模型答案”是两层状态。CherryIn 配置使用 `YUNNAN_API_KEY` 和 `agent/deepseek-v4-flash`；在 Windows 上运行 `scripts\set-yunnan-api-key.ps1` 安全录入密钥。所有模型凭据都必须保留在本机且不得提交。

## 既有数据适配边界

品种、审定、系谱、基因、别名、NCBI 定位和 GO 注释已完成只读接入。以下仍属后续映射范围，不能靠改变 PostgreSQL `search_path` 直接复用同名表：

1. `core.material` 到隆耘材料模型的稳定标识映射；
2. `core.experiment/trial_entry/phenotype_value` 等试验事实到隆耘发布状态、课题和证据卡片的权限映射；
3. `governance.*` 的标准、质控和导入审计与隆耘模板/规则版本的语义对齐；
4. 更大结果集的业务分页和缓存；当前智能体证据为有界只读结果。
