"""模型发布确认 → 绑定预测 端到端链路测试。

覆盖题设叙述：
1. 同模型版本 R1 先受理、R2 后受理，完成回执乱序到达（R2 先交齐）；
2. 路径存在但文件不可读的凭证不能发布，评估超时保持待确认；
3. 同一份 R2 完成凭证提交两次只产生一次发布归档；
4. 注册入口与预测入口报告同一可用版本/同一待确认结果；
5. 预测申请等待中重新提交直到受理；已受理后重复提交保留原任务；
6. 同版本另一跑次补齐发布后，已受理预测的绑定不变，详情显示实际采用的
   文件、入模字段与评估回执；
7. 禁止跨跑次补齐文件、入模列或评估。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import service_for
from app.services.model_release import (
    RUN_STATUS_PENDING_CONFIRM,
    RUN_STATUS_PENDING_EVALUATION,
    RUN_STATUS_PUBLISHED,
)
from app.store import store

MODEL_VERSION = "v2.3.0"
R1 = "RUN-R1"
R2 = "RUN-R2"
R3 = "RUN-R3"


def setup_artifacts():
    tmp = tempfile.mkdtemp(prefix="model_release_")
    readable = os.path.join(tmp, "readable_model.pkl")
    with open(readable, "wb") as handle:
        handle.write(b"fake-model-binary")
    # 路径存在但读取必然失败：目录不是文件
    unreadable_dir = tmp
    # 路径存在且是文件，但当前用户无读取权限
    unreadable_file = os.path.join(tmp, "no_permission.pkl")
    with open(unreadable_file, "wb") as handle:
        handle.write(b"secret")
    os.chmod(unreadable_file, 0o000)
    return readable, unreadable_dir, unreadable_file


def credential(run_code, model_file, columns, receipt_id, status, score=0.91):
    return {
        "run_id": run_code,
        "model_file": model_file,
        "input_columns": columns,
        "evaluation": {
            "run_id": run_code,
            "receipt_id": receipt_id,
            "status": status,
            "metrics": {"auc": score, "score": score},
        },
    }


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def run_scenario():
    release_svc = service_for("model_release")
    predict_svc = service_for("predict_batch")
    readable, unreadable_dir, unreadable_file = setup_artifacts()

    # ── Step 1: R1 先受理、R2 后受理，均进入待评估 ──
    a1 = release_svc.accept_training_run(R1, MODEL_VERSION)
    a2 = release_svc.accept_training_run(R2, MODEL_VERSION)
    assert_true(a1["ok"] and not a1["duplicate"], "R1 首次受理应成功")
    assert_true(a2["ok"] and not a2["duplicate"], "R2 首次受理应成功")
    assert_true(a1["run"]["受理序号"] < a2["run"]["受理序号"], "受理顺序应为 R1 先于 R2")
    assert_true(a1["run"]["status"] == RUN_STATUS_PENDING_EVALUATION, "受理后应为待评估")
    # 重复受理不新建跑次
    a1_again = release_svc.accept_training_run(R1, MODEL_VERSION)
    assert_true(a1_again["duplicate"] and a1_again["run"]["id"] == a1["run"]["id"], "重复受理沿用原跑次")

    # 尚未有产物时，注册入口与预测入口看到同一“待确认/不可预测”结论
    avail_before = release_svc.get_version_availability(MODEL_VERSION)
    assert_true(not avail_before["available"], "无发布产物时版本不可预测")
    assert_true(avail_before["状态"] == RUN_STATUS_PENDING_CONFIRM, "未发布版本应报告待确认")

    # ── Step 2: 路径存在但文件无法读取的凭证试发布（目录路径 + 无权限文件）──
    bad_dir = credential(R1, unreadable_dir, ["f1", "f2"], "EVAL-R1-X", "合格")
    res = release_svc.confirm_release(R1, bad_dir)
    assert_true(not res["ok"] and res["status"] == RUN_STATUS_PENDING_CONFIRM, "目录路径不能通过可读检查")
    assert_true(any("可读性" in err for err in res["errors"]), "应给出可读性校验失败原因")

    bad_perm = credential(R1, unreadable_file, ["f1", "f2"], "EVAL-R1-X", "合格")
    res = release_svc.confirm_release(R1, bad_perm)
    assert_true(not res["ok"], "无读取权限的文件不能发布")
    assert_true(release_svc.get_run(R1)["status"] == RUN_STATUS_PENDING_CONFIRM, "R1 应保持待确认")

    # 空路径同样不能靠“非空检查”蒙混
    bad_empty = credential(R1, "", ["f1"], "EVAL-R1-X", "合格")
    res = release_svc.confirm_release(R1, bad_empty)
    assert_true(not res["ok"] and any("模型文件" in err for err in res["errors"]), "空模型路径应被拒绝")

    # ── Step 3: R1 评估超时，即使文件可读也保持待确认 ──
    r1_timeout = credential(R1, readable, ["f1", "f2"], "EVAL-R1-T", "超时")
    res = release_svc.confirm_release(R1, r1_timeout)
    assert_true(not res["ok"], "评估超时不能发布")
    assert_true(release_svc.get_run(R1)["status"] == RUN_STATUS_PENDING_CONFIRM, "评估超时保持待确认")

    # 缺入模列 / 缺评估同样不能发布
    res = release_svc.confirm_release(R2, credential(R2, readable, [], "EVAL-R2", "合格"))
    assert_true(not res["ok"], "缺入模列不能发布")
    res = release_svc.confirm_release(
        R2, {"run_id": R2, "model_file": readable, "input_columns": ["f1"], "evaluation": {}}
    )
    assert_true(not res["ok"], "缺评估回执不能发布")

    # 禁止跨跑次：R1 的凭证里塞 R2 的合格评估
    cross = credential(R1, readable, ["f1"], "EVAL-R2", "合格")
    cross["evaluation"]["run_id"] = R2
    res = release_svc.confirm_release(R1, cross)
    assert_true(not res["ok"] and any("跨跑次" in err for err in res["errors"]), "跨跑次评估必须拒绝")

    # ── Step 4: R2 先交齐完整凭证，发布成功；同凭证提交两次只记一次 ──
    r2_columns = ["age", "income", "balance", "tenure"]
    r2_credential = credential(R2, readable, r2_columns, "EVAL-R2-OK", "合格", score=0.93)
    first = release_svc.confirm_release(R2, r2_credential)
    assert_true(first["ok"] and not first["duplicate"], "R2 凭证齐备应发布成功")
    first_confirmation = first["confirmation"]
    assert_true(release_svc.get_run(R2)["status"] == RUN_STATUS_PUBLISHED, "R2 应转为已发布")

    second = release_svc.confirm_release(R2, r2_credential)
    assert_true(second["ok"] and second["duplicate"], "重复提交同凭证应判重")
    assert_true(second["confirmation"]["id"] == first_confirmation["id"], "两次回执必须对应同一发布归档")
    assert_true(
        len([r for r in store.rows("model_release_confirmation") if r["跑次编号"] == R2]) == 1,
        "同一完成凭证只能产生一条确认记录",
    )

    # 注册入口与预测入口此时报告同一可用版本与同一组绑定
    avail = release_svc.get_version_availability(MODEL_VERSION)
    assert_true(avail["available"] and avail["状态"] == RUN_STATUS_PUBLISHED, "版本应已可用")
    assert_true(avail["来源跑次"] == R2, "可用产物应来自先交齐的 R2")
    assert_true(avail["模型文件"] == readable, "可用性结果应带固定模型文件")
    assert_true(avail["入模字段"] == r2_columns, "可用性结果应带入模字段")
    assert_true(avail["评估回执编号"] == "EVAL-R2-OK", "可用性结果应带评估回执")

    # ── Step 5a: 预测申请先于发布登记（等待中），发布后重新提交直到受理 ──
    app_code_wait = "APP-WAIT-1"
    # 用一个新版本演示等待分支：先登记申请，再发布
    wait_version = "v9.9.9"
    release_svc.accept_training_run("RUN-W1", wait_version)
    waiting = release_svc.register_prediction_application(app_code_wait, wait_version, "slice_wait")
    assert_true(waiting["受理状态"] == "等待中" and "predict_task" not in waiting, "无产物时申请应等待")
    assert_true(not waiting["availability"]["available"], "等待申请应附带同一待确认结论")
    # 发布该版本后重新提交同一申请
    w_cred = credential("RUN-W1", readable, ["x1"], "EVAL-W1", "合格")
    release_svc.confirm_release("RUN-W1", w_cred)
    re_submitted = release_svc.register_prediction_application(app_code_wait, wait_version, "slice_wait")
    assert_true(re_submitted["受理状态"] == "已受理", "等待中的申请重新提交后应受理")
    wait_task = re_submitted["predict_task"]
    assert_true(wait_task["绑定模型文件"] == readable, "受理任务应固定模型文件")

    # ── Step 5b: 对已发布版本登记申请，直接受理；再提交保留原任务 ──
    app_code = "APP-MAIN-1"
    accepted = release_svc.register_prediction_application(app_code, MODEL_VERSION, "slice_main")
    assert_true(accepted["受理状态"] == "已受理", "版本已发布，申请应直接受理")
    task = accepted["predict_task"]
    # 申请响应里的 availability 与注册入口查询结果是同一份结论
    assert_true(
        accepted["availability"] == release_svc.get_version_availability(MODEL_VERSION),
        "注册入口与预测入口必须报告同一可用版本结果",
    )
    # 任务创建瞬间固定绑定，全部来自 R2 同一跑次
    assert_true(task["绑定模型文件"] == readable, "任务应固定 R2 的模型文件")
    assert_true(task["绑定入模字段"] == r2_columns, "任务应固定 R2 的入模列")
    assert_true(task["绑定评估回执"]["receipt_id"] == "EVAL-R2-OK", "任务应固定 R2 的评估凭证")
    assert_true(task["来源跑次"] == R2 and task["发布确认编号"] == first_confirmation["确认编号"],
                "任务必须绑定来源跑次与发布归档")

    again = release_svc.register_prediction_application(app_code, MODEL_VERSION, "slice_main")
    assert_true(again["duplicate"] and again["受理状态"] == "已受理", "已受理申请重复提交仍受理")
    assert_true(again["application"]["预测任务编号"] == task["任务编号"], "已受理后必须保留原任务")

    # ── Step 6: 同版本另一跑次 R1 补齐完整凭证并发布，已受理预测绑定不变 ──
    r1_columns = ["age", "income", "balance", "tenure", "extra_flag"]
    r1_good = credential(R1, readable, r1_columns, "EVAL-R1-OK", "合格", score=0.88)
    r1_published = release_svc.confirm_release(R1, r1_good)
    assert_true(r1_published["ok"], "R1 补齐后应能发布")
    assert_true(r1_published["confirmation"]["id"] != first_confirmation["id"], "R1 是独立发布归档")

    # 重查已受理预测：任务与详情仍采用 R2 的文件、字段、回执
    detail = predict_svc.get_prediction_detail(task["id"])
    assert_true(detail["ok"], "预测详情应可查")
    assert_true(detail["绑定模型文件"] == readable, "详情应显示实际采用的模型文件")
    assert_true(detail["绑定入模字段"] == r2_columns, "详情应显示 R2 的入模字段，不能被 R1 覆盖")
    assert_true(detail["绑定评估回执"]["receipt_id"] == "EVAL-R2-OK", "详情应显示实际评估回执")
    assert_true(detail["来源跑次"] == R2, "详情来源跑次应保持 R2")
    assert_true(detail["发布确认编号"] == first_confirmation["确认编号"], "详情应保持原发布归档")

    # 版本可用性对同一版本保持稳定：仍指向首个发布归档 R2
    avail_after = release_svc.get_version_availability(MODEL_VERSION)
    assert_true(avail_after["来源跑次"] == R2, "版本可用绑定不应被后来的跑次顶替")

    # 执行预测后绑定依旧沿用，不被执行流程改写
    executed = predict_svc.execute_batch_and_collect_result(task["id"])
    assert_true(executed["ok"], "预测执行应成功")
    detail_after_run = predict_svc.get_prediction_detail(task["id"])
    assert_true(detail_after_run["绑定入模字段"] == r2_columns, "执行后绑定必须保持不变")
    assert_true(detail_after_run["绑定评估回执"]["receipt_id"] == "EVAL-R2-OK", "执行后评估凭证必须保持不变")

    # ── Step 7: 不能用另一跑次的材料给新跑次凑齐发布 ──
    release_svc.accept_training_run(R3, MODEL_VERSION)
    mixed = {
        "run_id": R3,
        "model_file": readable,
        "artifact_run_id": R2,  # 文件实际属于 R2
        "input_columns": r2_columns,
        "evaluation": {"run_id": R2, "receipt_id": "EVAL-R2-OK", "status": "合格", "metrics": {}},
    }
    res = release_svc.confirm_release(R3, mixed)
    assert_true(not res["ok"], "拼凑其他跑次产物不能发布")
    assert_true(any("跨跑次" in err for err in res["errors"]), "应明确报跨跑次绑定错误")
    assert_true(release_svc.get_run(R3)["status"] == RUN_STATUS_PENDING_CONFIRM, "R3 应停留待确认")

    print("[PASS] 模型发布确认 → 绑定预测 全链路叙述场景")


if __name__ == "__main__":
    run_scenario()
