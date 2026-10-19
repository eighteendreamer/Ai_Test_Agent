# 单订单创建与清理
POST http://127.0.0.1:49723/orders 不需要鉴权和请求体，每次创建一个订单，返回201，JSON键order.id是完整键（含点号）。
DELETE http://127.0.0.1:49723/orders/{id} 返回200及刚删除的订单JSON。必须用POST实际返回的order.id删除本次创建的记录，不得猜测ID。
原生工具api-test-runner的execute_task可调用这些接口。
