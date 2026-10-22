"""Select replay of known evidence without turning unknown sends into new calls."""


def generation_retry_strategy(task):
    if task.status != "failed":
        raise ValueError("只有明确失败的模块可以创建新重试任务")
    checkpoint = task.checkpoint
    if checkpoint and (checkpoint.receipt is not None or checkpoint.phase == "result_saved"):
        raise ValueError("模块已有结果收据，不能作为失败重试来源")
    # A provider failure response is evidence of a failed invocation, not a
    # generation payload that can be replayed into the draft parser.
    if task.error_code and task.error_code.startswith("model_"):
        return "frozen_request"
    if checkpoint and checkpoint.phase == "response_saved" and checkpoint.response is not None:
        return "saved_response"
    if checkpoint is not None or task.error_code == "call_outcome_unknown":
        raise ValueError("原调用结果未知且没有持久响应；不能创建重新发送任务")
    return "frozen_request"
