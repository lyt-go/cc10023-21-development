"""模型发布确认领域服务。

训练流水线只能登记“训练完成”，本服务补上“按实际训练产物确认发布”这一环：

1. 训练跑次（run）受理后进入「待评估」；
2. 操作员凭完成凭证发起发布确认，服务对模型文件可读性、入模字段、
   同一跑次的合格评估回执做实质校验，三者齐备才发布为「可预测模型产物」；
3. 同一份完成凭证重复提交只确认一次，返回原确认归档；
4. 批量预测只能基于「已发布」产物创建，任务创建后把模型文件、输入列、
   评估凭证和来源跑次固定下来，执行与详情都沿用这组绑定，
   不允许从其他跑次补齐缺列、文件或评估。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

# 跑次状态：受理 → 待评估；凭证不齐/评估超时 → 待确认；产物齐备 → 已发布
RUN_STATUS_PENDING_EVALUATION = "待评估"
RUN_STATUS_PENDING_CONFIRM = "待确认"
RUN_STATUS_PUBLISHED = "已发布"

# 评估回执只认可“合格”；“超时”等其他结论保持待确认
EVAL_QUALIFIED = "合格"
EVAL_TIMEOUT = "超时"

# 发布归档表 / 跑次表 / 预测申请表（均为内存仓库中的独立表）
TABLE_RUNS = "model_train_run"
TABLE_RELEASES = "model_release_confirmation"
TABLE_APPLICATIONS = "predict_application"


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def is_model_file_readable(path: str) -> bool:
    """模型文件可读性检查：路径非空且是文件，并且实际打开读取不抛错。

    路径存在不代表可读（目录、无权限、特殊设备都可能 open/read 失败），
    因此必须执行一次真实读取，不能用 os.path.exists 代替。
    """
    if not str(path or "").strip():
        return False
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as handle:
            handle.read(1)
    except OSError:
        return False
    return True


class ModelReleaseService(BaseModuleService):
    MODULE = "model_release"
    ENTITY = "模型发布确认"
    OPERATION_TERM = "确认"
    LIST_FIELDS = [
        "确认编号", "跑次编号", "模型版本", "模型文件", "入模字段", "评估回执编号", "发布状态",
    ]
    REQUIRED_FIELDS = ["跑次编号", "模型版本"]
    STATUS_ORDER = [RUN_STATUS_PENDING_EVALUATION, RUN_STATUS_PENDING_CONFIRM, RUN_STATUS_PUBLISHED]
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS: list[str] = []

    # ── 跑次受理 ───────────────────────────────────────────────────────
    def accept_training_run(
        self,
        run_code: str,
        model_version: str,
        *,
        pipeline_id: int | None = None,
    ) -> dict[str, Any]:
        """受理一次训练跑次。同一跑次编号重复受理只返回原记录，受理顺序按调用先后保留。"""
        run_code = str(run_code or "").strip()
        model_version = str(model_version or "").strip()
        if not run_code or not model_version:
            return {"ok": False, "message": "跑次编号与模型版本不能为空"}
        rows = store.rows(TABLE_RUNS)
        for existing in rows:
            if existing.get("跑次编号") == run_code:
                return {
                    "ok": True,
                    "duplicate": True,
                    "message": f"跑次 {run_code} 已受理，沿用原受理记录",
                    "run": existing,
                }
        run = {
            "id": _next_id(rows),
            "status": RUN_STATUS_PENDING_EVALUATION,
            "pending": True,
            "abnormal": False,
            "跑次编号": run_code,
            "模型版本": model_version,
            "关联流水线": pipeline_id,
            "受理序号": len(rows) + 1,
            "评估状态": "",
            "模型文件": "",
            "入模字段": [],
            "发布确认编号": None,
        }
        rows.append(run)
        return {"ok": True, "duplicate": False, "message": f"跑次 {run_code} 已受理，等待评估", "run": run}

    def get_run(self, run_code: str) -> dict[str, Any] | None:
        for row in store.rows(TABLE_RUNS):
            if row.get("跑次编号") == str(run_code):
                return row
        return None

    # ── 凭证指纹与校验 ─────────────────────────────────────────────────
    @staticmethod
    def _credential_fingerprint(credential: dict[str, Any]) -> str:
        canon = json.dumps(credential, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    def _validate_credential(
        self,
        run: dict[str, Any],
        credential: dict[str, Any],
    ) -> tuple[list[str], dict[str, Any]]:
        """对完成凭证做实质校验，返回（错误列表, 规范化凭证）。

        校验项：模型文件实际可读、入模列非空、同一跑次的合格评估回执齐备；
        凭证内任何跑次标识都必须与当前跑次一致，不能从别的跑次拼凑材料。
        """
        errors: list[str] = []
        model_file = str(credential.get("model_file") or "").strip()
        input_columns = credential.get("input_columns") or []
        if not isinstance(input_columns, (list, tuple)):
            input_columns = [str(input_columns)] if str(input_columns).strip() else []
        input_columns = [str(col).strip() for col in input_columns if str(col).strip()]
        evaluation = credential.get("evaluation") or {}
        if not isinstance(evaluation, dict):
            evaluation = {}

        run_code = run.get("跑次编号")

        # 1) 模型文件：必须真实可读，路径非空不算通过
        if not model_file:
            errors.append("缺少模型文件路径")
        elif not is_model_file_readable(model_file):
            errors.append(f"模型文件 {model_file} 存在性或可读性校验未通过")

        # 2) 入模字段：必须给出非空入模列
        if not input_columns:
            errors.append("缺少入模字段（入模列）")

        # 3) 评估回执：必须属于同一跑次，且结论为合格；超时保持待确认
        eval_run = str(evaluation.get("run_id") or "").strip()
        eval_receipt = str(evaluation.get("receipt_id") or "").strip()
        eval_status = str(evaluation.get("status") or "").strip()
        if not evaluation:
            errors.append("缺少评估回执")
        elif eval_run and eval_run != run_code:
            errors.append(f"评估回执属于跑次 {eval_run}，与当前跑次 {run_code} 不一致，禁止跨跑次绑定")
        elif not eval_receipt:
            errors.append("评估回执缺少回执编号")
        elif eval_status == EVAL_TIMEOUT:
            errors.append("评估回执显示评估超时，发布保持待确认")
        elif eval_status != EVAL_QUALIFIED:
            errors.append(f"评估回执结论为「{eval_status or '未知'}」，未达到合格标准")

        # 4) 凭证顶层/文件侧如带跑次标识，也必须同跑次，防止用别的跑次材料补齐
        claimed_run = str(credential.get("run_id") or "").strip()
        if claimed_run and claimed_run != run_code:
            errors.append(f"完成凭证归属跑次 {claimed_run}，与当前跑次 {run_code} 不一致")
        file_run = str(credential.get("artifact_run_id") or "").strip()
        if file_run and file_run != run_code:
            errors.append(f"模型产物归属跑次 {file_run}，与当前跑次 {run_code} 不一致，禁止跨跑次补齐文件")

        normalized = {
            "run_id": run_code,
            "model_file": model_file,
            "input_columns": input_columns,
            "evaluation": {
                "run_id": eval_run or run_code,
                "receipt_id": eval_receipt,
                "status": eval_status,
                "metrics": evaluation.get("metrics", {}),
            },
        }
        return errors, normalized

    # ── 发布确认 ───────────────────────────────────────────────────────
    def confirm_release(
        self,
        run_code: str,
        credential: dict[str, Any],
    ) -> dict[str, Any]:
        """凭完成凭证确认发布。凭证齐备则归档为可预测模型产物，否则保持待确认。"""
        run = self.get_run(run_code)
        if run is None:
            return {"ok": False, "message": f"跑次 {run_code} 尚未受理，无法确认发布"}
        credential = credential or {}
        fingerprint = self._credential_fingerprint(credential)
        release_rows = store.rows(TABLE_RELEASES)

        # 已发布跑次：任何重复提交都只返回原确认归档（同一完成凭证只记一次确认）
        if run.get("status") == RUN_STATUS_PUBLISHED and run.get("发布确认编号"):
            original = next(
                (row for row in release_rows if int(row.get("id", 0)) == int(run["发布确认编号"])),
                None,
            )
            if original is not None:
                return {
                    "ok": True,
                    "duplicate": True,
                    "message": "该跑次已发布，重复提交只返回原发布归档",
                    "confirmation": original,
                }

        # 同指纹凭证此前已确认过：直接返回原确认记录
        for existing in release_rows:
            if existing.get("凭证指纹") == fingerprint:
                return {
                    "ok": True,
                    "duplicate": True,
                    "message": "同一份完成凭证已确认过，返回原确认记录",
                    "confirmation": existing,
                }

        errors, normalized = self._validate_credential(run, credential)
        if errors:
            # 评估超时、文件不可读、材料缺失都停留在待确认
            run["status"] = RUN_STATUS_PENDING_CONFIRM
            run["pending"] = True
            run["abnormal"] = True
            run["模型文件"] = normalized["model_file"]
            run["入模字段"] = normalized["input_columns"]
            run["评估状态"] = normalized["evaluation"].get("status", "")
            return {
                "ok": False,
                "status": RUN_STATUS_PENDING_CONFIRM,
                "message": "发布确认未通过：" + "；".join(errors),
                "errors": errors,
                "run_id": run.get("跑次编号"),
            }

        # 三项齐备且同跑次：绑定为可预测模型产物并归档（只归档一次）
        confirmation = {
            "id": _next_id(release_rows),
            "status": RUN_STATUS_PUBLISHED,
            "pending": False,
            "abnormal": False,
            "确认编号": f"REL-{len(release_rows) + 1:04d}",
            "跑次编号": run.get("跑次编号"),
            "模型版本": run.get("模型版本"),
            "模型文件": normalized["model_file"],
            "入模字段": normalized["input_columns"],
            "评估回执编号": normalized["evaluation"]["receipt_id"],
            "评估回执": normalized["evaluation"],
            "凭证指纹": fingerprint,
            "发布状态": RUN_STATUS_PUBLISHED,
        }
        release_rows.append(confirmation)

        run["status"] = RUN_STATUS_PUBLISHED
        run["pending"] = False
        run["abnormal"] = False
        run["模型文件"] = normalized["model_file"]
        run["入模字段"] = normalized["input_columns"]
        run["评估状态"] = EVAL_QUALIFIED
        run["发布确认编号"] = confirmation["id"]

        self._sync_registry(run, confirmation)
        return {
            "ok": True,
            "duplicate": False,
            "message": f"跑次 {run.get('跑次编号')} 已发布为可预测模型产物",
            "confirmation": confirmation,
        }

    def _sync_registry(self, run: dict[str, Any], confirmation: dict[str, Any]) -> dict[str, Any]:
        """把已发布产物同步登记到模型注册表，使注册入口与预测入口看到同一可用版本。"""
        registry_rows = store.rows("model_registry")
        model_id = _next_id(registry_rows)
        entry = {
            "id": model_id,
            "status": "已注册",
            "pending": False,
            "abnormal": False,
            "模型编号": f"MODE-{model_id:04d}",
            "模型名称": f"model_{run.get('跑次编号')}_{run.get('模型版本')}",
            "算法类型": "xgboost",
            "训练特征": ",".join(confirmation.get("入模字段", [])),
            "模型版本": run.get("模型版本"),
            "模型文件": confirmation.get("模型文件"),
            "评估分数": str((confirmation.get("评估回执") or {}).get("metrics", {}).get("score", "")),
            "部署状态": "已发布",
            "来源跑次": run.get("跑次编号"),
            "发布确认编号": confirmation.get("确认编号"),
        }
        registry_rows.append(entry)
        return entry

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """通用资源路由读到的是发布确认归档（可预测模型产物）。"""
        rows = list(store.rows(TABLE_RELEASES))
        for field, expected in (filters or {}).items():
            if field in self.LIST_FIELDS:
                rows = [
                    row for row in rows
                    if expected.casefold() in str(row.get(field, "")).casefold()
                ]
        if keyword and self.LIST_FIELDS:
            field = self.LIST_FIELDS[0]
            rows = [row for row in rows if keyword.casefold() in str(row.get(field, "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return next(
            (row for row in store.rows(TABLE_RELEASES) if int(row.get("id", 0)) == entry_id),
            None,
        )

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """发布确认只能凭完成凭证走 confirm_release，禁止通用登记绕过实质校验。"""
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail=(
                "模型发布确认不支持直接登记：请先调用 /api/model-release/runs/accept 受理跑次，"
                "再凭完成凭证调用 /api/model-release/runs/{run_code}/confirm 完成发布校验"
            ),
        )

    def list_release_confirmations(self) -> list[dict[str, Any]]:
        return store.rows(TABLE_RELEASES)

    def domain_snapshot(self) -> dict[str, Any]:
        rows = store.rows(TABLE_RELEASES)
        run_rows = store.rows(TABLE_RUNS)
        return {
            "module": self.MODULE,
            "entity": self.ENTITY,
            "artifact": "可预测模型产物发布归档",
            "count": len(rows),
            "by_status": {
                RUN_STATUS_PENDING_EVALUATION: sum(
                    1 for row in run_rows if row.get("status") == RUN_STATUS_PENDING_EVALUATION),
                RUN_STATUS_PENDING_CONFIRM: sum(
                    1 for row in run_rows if row.get("status") == RUN_STATUS_PENDING_CONFIRM),
                RUN_STATUS_PUBLISHED: len(rows),
            },
            "abnormal": sum(1 for row in run_rows if row.get("abnormal")),
        }

    # ── 版本可用性：注册入口与预测入口共用同一份结论 ───────────────────
    def get_version_availability(self, model_version: str) -> dict[str, Any]:
        """报告某模型版本当前能否开始预测。

        有已发布产物 → available=True 并给出该版本固定绑定；
        否则返回待确认/待评估结论，注册入口和预测入口调用同一方法，结果必然一致。
        """
        model_version = str(model_version or "").strip()
        release_rows = store.rows(TABLE_RELEASES)
        published = [
            row for row in release_rows
            if row.get("模型版本") == model_version and row.get("发布状态") == RUN_STATUS_PUBLISHED
        ]
        if published:
            # 完成回执可能乱序到达，已发布产物以首个发布归档为准，保持版本绑定稳定
            first = min(published, key=lambda row: int(row.get("id", 0)))
            return {
                "ok": True,
                "available": True,
                "模型版本": model_version,
                "状态": RUN_STATUS_PUBLISHED,
                "发布确认编号": first.get("确认编号"),
                "来源跑次": first.get("跑次编号"),
                "模型文件": first.get("模型文件"),
                "入模字段": list(first.get("入模字段", [])),
                "评估回执编号": first.get("评估回执编号"),
                "评估回执": first.get("评估回执"),
                "message": f"模型版本 {model_version} 已有可预测模型产物，可开始预测",
            }

        runs = [row for row in store.rows(TABLE_RUNS) if row.get("模型版本") == model_version]
        if not runs:
            return {
                "ok": True,
                "available": False,
                "模型版本": model_version,
                "状态": "未受理",
                "message": f"模型版本 {model_version} 暂无训练跑次",
            }
        # 只要还有跑次未发布，操作员看到的就是待确认（含评估超时、凭证不齐）
        status = RUN_STATUS_PENDING_CONFIRM if any(
            row.get("status") != RUN_STATUS_PUBLISHED for row in runs
        ) else RUN_STATUS_PENDING_EVALUATION
        return {
            "ok": True,
            "available": False,
            "模型版本": model_version,
            "状态": status,
            "待确认跑次": [row.get("跑次编号") for row in runs if row.get("status") != RUN_STATUS_PUBLISHED],
            "message": f"模型版本 {model_version} 尚无已发布产物，当前批次不能开始预测",
        }

    # ── 预测申请：等待或受理，受理后绑定固定 ───────────────────────────
    def register_prediction_application(
        self,
        application_code: str,
        model_version: str,
        data_slice: str,
    ) -> dict[str, Any]:
        """操作员登记一次预测申请。

        版本已有发布产物 → 受理并创建绑定固定的批量预测任务；
        否则申请保持等待，等待期间不创建任务、不预占绑定。
        重复提交同一申请编号：等待中的重新判定；已受理的保留原任务。
        """
        from app.services import service_for

        application_code = str(application_code or "").strip()
        model_version = str(model_version or "").strip()
        data_slice = str(data_slice or "").strip()
        if not application_code or not model_version:
            return {"ok": False, "message": "申请编号与模型版本不能为空"}

        app_rows = store.rows(TABLE_APPLICATIONS)
        existing = next((row for row in app_rows if row.get("申请编号") == application_code), None)
        availability = self.get_version_availability(model_version)

        if existing is not None:
            if existing.get("受理状态") == "已受理" and existing.get("预测任务编号"):
                # 已受理任务不因后续跑次/凭证而改变绑定
                return {
                    "ok": True,
                    "duplicate": True,
                    "受理状态": "已受理",
                    "message": "预测申请已受理，保留原预测任务及其固定绑定",
                    "application": existing,
                    "availability": availability,
                }
            # 等待中的申请：重新判定，满足条件则受理
            if availability["available"]:
                return self._accept_application(existing, availability, service_for("predict_batch"))
            existing["受理状态"] = "等待中"
            return {
                "ok": True,
                "duplicate": True,
                "受理状态": "等待中",
                "message": "预测申请仍在等待该版本发布可预测产物",
                "application": existing,
                "availability": availability,
            }

        application = {
            "id": _next_id(app_rows),
            "status": "等待中",
            "pending": True,
            "abnormal": False,
            "申请编号": application_code,
            "模型版本": model_version,
            "数据切片": data_slice,
            "受理状态": "等待中",
            "预测任务编号": None,
        }
        app_rows.append(application)

        if availability["available"]:
            return self._accept_application(application, availability, service_for("predict_batch"))
        return {
            "ok": True,
            "duplicate": False,
            "受理状态": "等待中",
            "message": "预测申请已登记，等待模型版本发布后受理",
            "application": application,
            "availability": availability,
        }

    def _accept_application(
        self,
        application: dict[str, Any],
        availability: dict[str, Any],
        predict_service: Any,
    ) -> dict[str, Any]:
        created = predict_service.create_bound_prediction_task(
            model_version=availability["模型版本"],
            model_file=availability["模型文件"],
            input_columns=availability["入模字段"],
            evaluation_receipt=availability["评估回执"],
            source_run=availability["来源跑次"],
            release_code=availability["发布确认编号"],
            data_slice=application.get("数据切片") or "full_dataset_slice_001",
        )
        if not created.get("ok"):
            application["受理状态"] = "等待中"
            return {
                "ok": False,
                "受理状态": "等待中",
                "message": f"预测任务创建失败，申请保持等待：{created.get('message', '发布产物绑定不完整')}",
                "application": application,
                "availability": availability,
            }
        task = created["task"]
        application["受理状态"] = "已受理"
        application["status"] = "已受理"
        application["pending"] = False
        application["预测任务编号"] = task["任务编号"]
        application["预测任务ID"] = task["id"]
        return {
            "ok": True,
            "duplicate": False,
            "受理状态": "已受理",
            "message": "预测申请已受理，批量预测任务按发布产物固定绑定创建",
            "application": application,
            "availability": availability,
            "predict_task": task,
        }

    def get_application(self, application_code: str) -> dict[str, Any] | None:
        for row in store.rows(TABLE_APPLICATIONS):
            if row.get("申请编号") == str(application_code):
                return row
        return None
