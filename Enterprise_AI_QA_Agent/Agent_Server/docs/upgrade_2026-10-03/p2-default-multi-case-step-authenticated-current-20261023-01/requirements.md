# 订单接口
POST http://127.0.0.1:60486/orders 无需鉴权，创建一条订单，返回201和JSON顶层字面量键 order.id（含点号，不是嵌套路径）。
GET http://127.0.0.1:60486/orders/{id} 返回订单详情；有效ID返回200，字段为 flag=false、count=1、nullable=null。
DELETE http://127.0.0.1:60486/orders/{id} 删除订单并返回200。
