# 直接关系采集与本地祖先图

主服务为 pedigree-direct（profile pedigree-direct）。完整网页展开程序及所有旧断点、
source_verified 快照保留为核验工具，旧 normal/complex 服务停止主采集，不删除数据。

先执行 pedigree-direct.sql。两个新表分别保存直接关系证据与来源材料待采集任务。
新来源材料按正整数来源编号加原文名称识别；同名不合并，无编号中间节点按所属关系片段与位置隔离。
采集只展开当前材料及其无编号中间组合，遇到具名、带来源编号的材料即转为待采集引用。
任务优先补齐发现的亲本，每个来源材料独立请求一次自己的直接关系。来源 ID 或名称不符不能强行替换。

启动前用 verify_pedigree_direct.py 对归档全树比较所有名称路径及重复出现次数。
原站问号/零值名称保留原文作为审计证据，界面显示“亲本名称待核实”，不计为完整图谱。
完整来源快照继续优先展示；没有完整快照的品种可展示直接关系及本地递归图，缺失、冲突、回环需明确提示。
回环的内部关系不作为有效親缘关系展示。原始片段保留，不删除、不填造、不静默修正原站事实。

已有 HTML 来源证据会用于初始化直接关系，不复用旧错误的 SQLite 全局节点/边。
初始化后继续采集所有目录材料以及新发现的祖先材料。断点位于 /data/research/pedigree-direct。
进度中的 complete 仅指直接亲本片段完成，不等于全库祖先图完整。
direct_queue_finished 仅表示直接采集队列结束；来源缺失、needs_review、冲突和回环仍需核验。
复用数据库不是新科学事实的证明，图谱完整只表示原站提供的有效关系已覆盖，不代表未知祖先有记录。

沿用 /data/research/pedigree-rebuild/request-rate.lock 及 source-blocked.json，
不提高原站请求启动频率，不绕过 403/429。部署后需更新定时检查，监控新服务和新进度路径。

## 2026年10月16日专项排查

27项 needs_review 已离线回放并逐项重新请求原站：不同具名节点具有相同 TreeView toggle 参数，点击后已有分支被折叠。采集器现在将此情形报告为 SourceExpansionAmbiguity，直接进入 needs_review，不将其当作网络故障重复重试。原始名称、来源 HTML、点击前后页面及丢失路径保存在 `/data/research/pedigree-direct/recovery-audit-20261016/audit.json` 及相邻证据文件；没有将无法展开的节点当作已知叶节点。恢复需原站修复或有授权的关系导出，不按复合名称猜测亲本。

`recover_pedigree_aliases.py` 仅处理两项明确、同来源 ID 的名称差异。先核实来源引用中的 ID 和原名，并重新获取独立档案的直接关系，与既存签名一致后，才以原系谱名保存带证据的关系片段。保留已有事实和原始简称，不做全库按名称或单独 ID 的无条件合并。

荃广优8238的上述名称缺口已补齐，本地/API图谱336节点405关系，但独立全树核验未完成前不得宣布 source verified。独立容器为 `ynaas-trial-pedigree-verify-631806`，结果在 `recovery-audit-20261016/independent-631806/result.json`。`verified` 必须同时满足全树确实展开完毕，名称路径及出现次数完全一致；`source_collection_incomplete` 或 `differences_found` 均不通过。其共享限速不变，运行时不要开启第二个原站采集通道。

修复采集镜像为 `ynaas-trial-api:pedigree-recovery-20261016`，运行时最后追加服务器 `/home/lijian/apps/ynaas-trial-direct-9b12472/audit/recovery-worker.yml`；它只覆盖采集服务镜像，不能替换 API/Web 的已部署版本。旧采集队列在无 pending 时正常退出，27项来源缺陷未解决时保持 needs_review，不盲目重排。
