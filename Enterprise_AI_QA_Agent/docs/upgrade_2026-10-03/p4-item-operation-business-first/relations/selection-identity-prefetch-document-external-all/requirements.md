# 实际查询及准备清理
POST http://127.0.0.1:60216/orders 不需要鉴权和请求体，每次创建一个订单，返回201，JSON键order.id是完整键（含点号）。
本次需要准备两条订单，第二条作为未查询的独立记录，两个ID均要保存。第一条count=1，第二条count=0；flag=false、nullable=null。
GET http://127.0.0.1:60216/orders/{id} 返回200及该订单完整JSON。DELETE同一路径返回200及刚删除的订单JSON。
查询第一条并验证其返回；最后逐条清理本次两个订单。没有已存在ID，需依据本文件准备，不能猜ID。原生工具api-test-runner的execute_task可调用这些接口。
