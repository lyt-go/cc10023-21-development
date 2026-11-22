"""模型发布确认与绑定预测专用路由。

通用资源路由只负责列表/登记/动作；发布确认涉及凭证实质校验、版本可用性
和预测申请受理，业务判断全部留在 ModelReleaseService / PredictBatchService，
路由层只做参数解包。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import service_for
from app.services.model_release import ModelReleaseService
from app.services.predict_batch import PredictBatchService

router = APIRouter(prefix="/api/model-release", tags=["模型发布确认"])


class AcceptRunPayload(BaseModel):
    run_code: str
    model_version: str
    pipeline_id: int | None = None


class ConfirmReleasePayload(BaseModel):
    credential: dict[str, Any] = Field(default_factory=dict)


class PredictionApplicationPayload(BaseModel):
    application_code: str
    model_version: str
    data_slice: str = "full_dataset_slice_001"


def release_service() -> ModelReleaseService:
    return service_for("model_release")  # type: ignore[return-value]


def predict_service() -> PredictBatchService:
    return service_for("predict_batch")  # type: ignore[return-value]


@router.post("/runs/accept")
def accept_run(payload: AcceptRunPayload) -> dict[str, Any]:
    """受理训练跑次，进入待评估；重复受理沿用原记录并保留受理顺序。"""
    return release_service().accept_training_run(
        payload.run_code.strip(),
        payload.model_version.strip(),
        pipeline_id=payload.pipeline_id,
    )


@router.post("/runs/{run_code}/confirm")
def confirm_release(run_code: str, payload: ConfirmReleasePayload) -> dict[str, Any]:
    """凭完成凭证确认发布：可读文件、入模列、同跑次合格评估齐备才发布。"""
    return release_service().confirm_release(run_code, payload.credential)


@router.get("/confirmations")
def list_confirmations() -> dict[str, Any]:
    items = release_service().list_release_confirmations()
    return {"ok": True, "total": len(items), "items": items}


@router.get("/versions/{model_version}/availability")
def version_availability(model_version: str) -> dict[str, Any]:
    """模型注册中心入口：报告版本当前可用版本或待确认结果。"""
    return release_service().get_version_availability(model_version)


@router.post("/predict-applications")
def register_prediction_application(payload: PredictionApplicationPayload) -> dict[str, Any]:
    """预测入口登记申请：与注册入口共用同一份版本可用性结论。"""
    return release_service().register_prediction_application(
        payload.application_code.strip(),
        payload.model_version.strip(),
        payload.data_slice.strip(),
    )


@router.get("/predict-applications/{application_code}")
def get_prediction_application(application_code: str) -> dict[str, Any]:
    application = release_service().get_application(application_code)
    if application is None:
        return {"ok": False, "message": f"预测申请 {application_code} 不存在"}
    availability = release_service().get_version_availability(application.get("模型版本", ""))
    return {"ok": True, "application": application, "availability": availability}


@router.get("/predict-tasks/{task_id}/detail")
def prediction_task_detail(task_id: int) -> dict[str, Any]:
    """预测详情：实际采用的模型文件、入模字段与评估回执（创建时固定的绑定）。"""
    return predict_service().get_prediction_detail(task_id)
