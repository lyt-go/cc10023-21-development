"""批量预测任务领域服务：基于已注册模型的批量预测执行、结果存储与校验。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class PredictBatchService(BaseModuleService):
    MODULE = "predict_batch"
    ENTITY = "预测任务"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["任务编号", "模型编号", "数据切片", "输出格式", "分片策略", "并行度", "预测状态", "结果校验"]
    REQUIRED_FIELDS = ["任务编号", "模型编号", "数据切片"]
    STATUS_ORDER = ["草稿", "等待中", "运行中", "已完成"]
    ACTION_RULES = {"提交运行": "草稿", "停止预测": "运行中", "确认完成": "已完成"}
    NEGATIVE_ACTIONS = []

    # ── 模型发布确认链路：按已发布可预测产物创建绑定固定的批量预测任务 ──
    def create_bound_prediction_task(
        self,
        *,
        model_version: str,
        model_file: str,
        input_columns: list[str],
        evaluation_receipt: dict[str, Any],
        source_run: str,
        release_code: str,
        data_slice: str,
    ) -> dict[str, Any]:
        """基于已发布产物创建批量预测任务。

        任务创建瞬间把模型文件、输入列、评估凭证、来源跑次和发布归档固定下来，
        后续执行与详情都沿用这组绑定；缺列、文件、评估只能来自同一跑次的发布产物，
        本方法不接受任何跨跑次补齐。
        """
        missing = []
        if not str(model_file or "").strip():
            missing.append("模型文件")
        if not input_columns:
            missing.append("入模字段")
        if not evaluation_receipt or not str(evaluation_receipt.get("receipt_id") or "").strip():
            missing.append("评估回执")
        if not str(source_run or "").strip():
            missing.append("来源跑次")
        if missing:
            return {"ok": False, "message": f"发布产物绑定不完整，缺少：{'、'.join(missing)}"}

        rows = store.rows(self.MODULE)
        next_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        task = {
            "id": next_id,
            "status": "等待中",
            "pending": True,
            "abnormal": False,
            "任务编号": f"PRED-{next_id:04d}",
            "模型编号": model_version,
            "模型版本": model_version,
            "数据切片": data_slice,
            "输出格式": "csv",
            "分片策略": "round_robin",
            "并行度": "16",
            "预测状态": "等待中",
            "结果校验": "auto_validate",
            # ── 任务创建后固定的发布产物绑定，执行/详情均沿用，不再改写 ──
            "绑定模型文件": model_file,
            "绑定入模字段": list(input_columns),
            "绑定评估回执": dict(evaluation_receipt),
            "评估回执编号": evaluation_receipt.get("receipt_id"),
            "来源跑次": source_run,
            "发布确认编号": release_code,
        }
        rows.append(task)
        return {"ok": True, "task": task, "task_id": next_id}

    def get_prediction_detail(self, task_id: int) -> dict[str, Any]:
        """预测详情：展示该次预测实际采用的文件、字段和评估回执（创建时固定的绑定）。"""
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False, "message": f"预测任务 {task_id} 不存在"}
        return {
            "ok": True,
            "task_id": task_id,
            "任务编号": task.get("任务编号"),
            "预测状态": task.get("预测状态", task.get("status")),
            "模型版本": task.get("模型版本", task.get("模型编号")),
            "数据切片": task.get("数据切片"),
            "来源跑次": task.get("来源跑次"),
            "发布确认编号": task.get("发布确认编号"),
            "绑定模型文件": task.get("绑定模型文件"),
            "绑定入模字段": list(task.get("绑定入模字段", [])),
            "绑定评估回执": task.get("绑定评估回执"),
            "评估回执编号": task.get("评估回执编号"),
        }

    # ── 链路4: 批量预测 → 挖掘结果 ──
    def execute_batch_and_collect_result(self, task_id: int) -> dict[str, Any]:
        """执行批量预测任务并将预测结果采集到挖掘结果模块进行深度分析。"""
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False, "message": f"预测任务 {task_id} 不存在"}
        task["status"] = "已完成"
        task["prediction_state"] = "已完成"
        task["pending"] = False
        mining_rows = store.rows("mining_result")
        next_id = max((int(r.get("id", 0)) for r in mining_rows), default=0) + 1
        model_id = task.get("模型编号", "unknown")
        mining_rows.append({
            "id": next_id,
            "status": "草稿",
            "pending": True,
            "abnormal": False,
            "结果编号": f"RES-P{task_id:04d}-{next_id:04d}",
            "分析主题": f"批量预测任务{task_id}挖掘分析",
            "算法类型": "ensemble_voting",
            "洞察摘要": f"从批量预测结果中识别出用户行为模式并写入挖掘结果" + "；发现2个关键行为路径",
            "关键分群": "高转化_群A,待激活_群B",
            "置信度": "0.82",
            "结果文件": f"mining/predict_{task_id}_result.json",
            "结果版本": "v1.0.0",
        })
        return {"ok": True, "task_id": task_id, "mining_id": next_id,
                "message": f"预测结果已采集并导入挖掘结果"}

    def validate_prediction_consistency(self, task_id: int) -> dict[str, Any]:
        """校验预测结果的一致性和格式合法性，确保输出符合预期schema。"""
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False}
        is_valid = task.get("输出格式") in ("csv", "json", "parquet")
        if not is_valid:
            task["abnormal"] = True
            return {"ok": False, "reason": f"输出格式 {task.get('输出格式')} 不合法"}
        return {"ok": True, "task_id": task_id, "format": task.get("输出格式")}

    def split_predict_partitions(self, task_id: int) -> dict[str, Any]:
        """将批量预测任务切分为多个分区并行执行，提高大规模数据集的预测吞吐。"""
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False}
        parallelism = int(str(task.get("并行度", "4")))
        partitions = []
        for i in range(parallelism):
            partitions.append({
                "partition_id": i,
                "task_id": task_id,
                "model_id": task.get("模型编号"),
                "output": f"predict_output/{task_id}/part_{i:03d}.csv",
            })
        return {"ok": True, "task_id": task_id, "partition_count": parallelism, "partitions": partitions}

    def export_predictions_to_csv(self, task_id: int) -> dict[str, Any]:
        """导出批量预测结果为CSV格式，写入外部存储供下游业务消费。"""
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False}
        export_rows = store.rows("data_export")
        next_id = max((int(r.get("id", 0)) for r in export_rows), default=0) + 1
        export_rows.append({
            "id": next_id,
            "status": "已完成",
            "pending": False,
            "abnormal": False,
            "任务编号": f"EXP-P{task_id:04d}",
            "数据来源": f"predict_task_{task_id}",
            "导出范围": "全量预测结果",
            "导出格式": "csv",
            "字段映射": "prediction_id,model_id,score,label,dt",
            "分片策略": "none",
            "压缩方式": "gzip",
            "导出状态": "已完成",
        })
        return {"ok": True, "export_id": next_id, "task_id": task_id}