# C2 环境适用确认接入实际执行事务

研究基线：bbea328e。先读原目标、完整方案及C1，再查run_store的创建/领取/开始/恢复/依赖结果、RunService、ExecutionService、RedisDispatcher、追加确认的项目锁及原Agent检查点；本地《Agent轨迹与协议鲁棒性》04.5采用真实副作用和恢复状态核对，课程阈值不作为产品事实。

## 依据与纠正

1. C1创建事务只核对原PG固定依据，并未查当前确认；已保存计划可能仍选取后来明确失效的精确版本。需要在原事务读当前确认，不改原计划/hash或把全项目停用。
2. 现有`_block_failed_dependencies_in_cursor`和`blocked_dependency_records`已提供未执行业务的终态结果、真实payload hash和依赖传播。采用同一Attempt/Result体系，禁止另建阻断平台。确认失效只阻止这个固定快照中的精确版本；未提及/unknown/撤回不自动阻断。
3. 追加确认先锁project；运行创建同样如此。绑定运行领取/开始应先锁project再锁item/attempt，保证确认先提交则本次看到新记录；不得先锁run再锁item，原依赖代码已说明该顺序会死锁。项目锁内不发模型、HTTP或读MySQL。
4. `_recover_expired_sync`会把已运行项重新排队并清空started_at，但历史Attempt保留started_at/checkpoint。不得以queued或当前started_at为空证明业务未发生。明确失效也不能把这种恢复伪报为从未执行，原恢复检查点保持；已开始的动作与后续动作修订另行处理。
5. `ExecutionService.execute_item`假设start一定返回running。若确认在领取后变化，start需要在同事务保存blocked结果；执行消费方必须返回该结果，不能继续查用例/起心跳/发工具。RunService事件也不能把它报为item_started。既有公开类型已支持blocked，结果actual支持结构化证据，不需要新测试模式。

## 本次采用的实现

在PostgresBusinessPlanning复用固定核对及按需求版本取确认头，提取精确用例的明确invalid；同需求/点出现相互矛盾的明确适用或未知评审时不能伪造一致，按高置信度原则不硬拦该冲突点。结果保存实际采用的完整确认、快照/hash、原规划依据hash、阶段/时间及精确版本，明确business_execution_started=false。

新运行创建在原suite/run/items/Outbox事务产生直接失效项和必要依赖的未执行结果，独立项仍排队；原请求恢复在这些检查前返回原运行。绑定运行领取在原领取事务阻止未开始的失效项，限制不消耗独立项的limit；开始在项目锁内再次核对并处理领取后的变化。终态不复用或重置；撤回只影响尚未执行的新条目，已保存blocked运行保持原结果，重试用新Run。

本次先真实复现创建漏洞，再用MD/DOCX/PDF及独立业务HTTP验证三个阶段、必要前置/独立项、撤回/unknown/其他目标、并发、丢回执和已开始恢复。配置仍MySQL，任务/确认/运行及结果PG，原件RustFS；不新增依赖、配置键或业务表。允许通过项的当前决定持久回执、已开始后的逐动作修订、新目标回归和正式页面继续按完整C2/D推进；不能把这一次限制当作完整环境产品或P0—P5完成。

## 实际验证与剩余边界

原实现创建漏洞两轮各1失败，其中第二轮保存完整失败输入；三格式创建3通过，扩展9通过/1收集警告；修正测试导入别名后12通过/0跳过（119.22秒）。实际PG项目锁竞争保存transactionid等待和blocking PID；确认在领取之后提交，开始会读取新invalid。已开始POST/检查点经过期恢复保持，恢复0新HTTP；静态目标不证明业务状态改变，自主续跑未调用。独立项实际GET及原PG结果核验200/9/0/False/null通过。

既有链路回归132通过/2外部模型跳过（277.48秒）。独立生产前两轮各1失败，捕获确认仅PG时间+00:00与API的Z不同；采用既有Run模型读取比较，最终1通过（99.22秒）。实际API/规划CLI/执行CLI及真实存储验证创建即blocked、终态派发0业务HTTP、撤回/重启/归档/删配置后原结果恢复。模型物理发送4条，均为受控模型；旧日志/失败/原件不覆盖。完整命令与审计见IMPLEMENTATION.md及p4-environment-restrictions-validation.json。

本次完成C2a，不能据此宣称C2完整。下一步先研究如何保存允许通过项当前决定回执、原检查点及动作修订，再验证未知副作用和新目标回归；正式环境页面/AI候选/默认Agent及全部P0—P5继续。
