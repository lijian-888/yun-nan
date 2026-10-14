# 原站完整系谱重采

旧全局拼接系谱的发布已停用。本地旧采集目录的 sync_to_postgres.py
继续导入品种、审定和性状，但不再调用 insert_pedigree；
retire-legacy-pedigree.patch 留存该切换供其他旧采集副本参考。
不要继续把旧 SQLite 的全局 pedigree_nodes/edges 导出为有效系谱。

先执行 pedigree-verification.sql，再发布 API 和前端。
该迁移保留全部旧快照；source_verified 默认 false。
系谱页面和专用智能体系谱证据仅采用校验通过的新快照。

重采服务：

    RELEASE_TAG=<release> docker-compose --env-file deploy/ynaas-193/.env +      -f docker-compose.lan.yml -f <release>/deploy/ynaas-193/compose.override.yml +      -f <release>/deploy/ynaas-193/pedigree-worker.yml +      -p ynaas-trial --profile pedigree-rebuild up -d --no-deps pedigree-rebuild

进度和原始页面保存在 research_data 卷的 /data/research/pedigree-rebuild。
progress.json 记录当前品种、已校验数量、失败项和完成状态。
服务重启时跳过已有 source_verified 快照，从未完成品种继续。
普通、复杂两个进程分别运行 normal/complex lane；同时启动 pedigree-rebuild 和 pedigree-complex。
每个品种在普通通道最多运行约 60 秒或展开 40 次（一次在途请求/解析可超过时间片）。
尚未完成时将经过校验的原站 HTML、ViewState、公开站点 cookie 和展开列表写入 checkpoint.json，
再转交复杂通道按时间片轮转继续；绝不发布部分结果。每个品种独占文件锁，防止重复入库。
两条通道共享请求锁，总体请求启动间隔至少 1 秒；同一原站 ASP.NET 树的展开不能并行。
每次响应只构建一次文档；运行环境有 lxml 时使用它，否则使用 html.parser。
progress.json/complex-progress.json 每个已校验响应后更新 branch：展开次数、待展开分支数、
已发现节点数、页面大小、累计网络耗时和累计解析耗时。日志每约 15 秒更新一次。
待展开数可能随深入祖先增加，不是最终总数，不应显示成百分比。
deferred/*.json 保存复杂任务和错误信息；错误最多三次，最终 needs_review 必须人工检查。
normal_queue_finished 仅表示普通通道结束；只有全部品种都有有效快照才报告 completed。
403/429 写 source-blocked.json 并停止两条通道，需管理员确认原站许可/恢复情况后处理；不绕过限制。
单品种遇到网络错误、状态丢失、展开未结束或保护上限，均不得发布。

完整性定义：

- 当前品种页面内所有可展开节点都已展开，不按 ID 或斜杠猜测亲本。
- 每条显示名称和分支上下文保留；0 是参数，不能作为未知亲本名称。
- 展开后不能丢失已有分支；正整数链接、名称及完整子树全部相同时才复用共同祖先。
- 入库计数与核验计划相同，同一事务写入快照、节点、关系和证据摘要。
- 完整表示采集时原站可展示的全部关系，不表示原站记录了全部生物学祖先。

数据库原始归档仍可用于管理员审计，但其 source_verified=false 不应被当作已确认关系。
