# 完整查询与清理
准备两条记录：POST http://127.0.0.1:61728/orders，每次返回201及order.id。
GET http://127.0.0.1:61728/orders/{id}返回200、flag=false、count=1、nullable=null。
DELETE http://127.0.0.1:61728/orders/{id}返回200，清理本次创建的全部记录。
