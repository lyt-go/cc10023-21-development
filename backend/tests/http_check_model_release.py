"""模型发布确认 HTTP 端到端核验（仅用标准库，python -I 运行）。

对运行中的后端逐条验证题设叙述：不可读文件拒绝、评估超时待确认、
同凭证重复提交同一归档、注册/预测入口同一结论、申请等待→受理、
已受理保留原任务、详情固定绑定、跨跑次补齐拒绝。
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request

BASE = os.environ.get("MODEL_RELEASE_BASE", "http://127.0.0.1:8011/api/model-release")
VERSION = "v3.1.0"
R1, R2, R3 = "HTTP-R1", "HTTP-R2", "HTTP-R3"


def call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - 诊断用
        raise AssertionError(f"HTTP {exc.code}: {exc.read().decode('utf-8')}") from exc


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def cred(run_code, model_file, cols, receipt, status, score=0.9):
    return {
        "run_id": run_code,
        "model_file": model_file,
        "input_columns": cols,
        "evaluation": {"run_id": run_code, "receipt_id": receipt, "status": status,
                       "metrics": {"auc": score, "score": score}},
    }


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="http_model_release_")
    readable = os.path.join(tmp, "model.pkl")
    with open(readable, "wb") as fh:
        fh.write(b"model-bytes")
    no_perm = os.path.join(tmp, "locked.pkl")
    with open(no_perm, "wb") as fh:
        fh.write(b"x")
    os.chmod(no_perm, 0o000)

    print("1) 受理顺序 R1 先 R2 后，初始待评估")
    a1 = call("POST", "/runs/accept", {"run_code": R1, "model_version": VERSION})
    a2 = call("POST", "/runs/accept", {"run_code": R2, "model_version": VERSION})
    check(a1["ok"] and a2["ok"], "两个跑次受理成功")
    check(a1["run"]["受理序号"] < a2["run"]["受理序号"], "受理顺序 R1 先于 R2")
    check(a1["run"]["status"] == "待评估", "受理后状态为待评估")

    print("2) 无产物时注册/预测入口同一待确认结论")
    avail = call("GET", f"/versions/{VERSION}/availability")
    check(avail["available"] is False and avail["状态"] == "待确认", "版本报告待确认、不可预测")

    print("3) 路径存在但不可读（目录/无权限/空路径）一律拒绝")
    r = call("POST", f"/runs/{R1}/confirm", {"credential": cred(R1, tmp, ["f1"], "E1", "合格")})
    check(not r["ok"] and r["status"] == "待确认", "目录路径不通过可读检查")
    r = call("POST", f"/runs/{R1}/confirm", {"credential": cred(R1, no_perm, ["f1"], "E1", "合格")})
    check(not r["ok"], "无权限文件不通过可读检查")
    r = call("POST", f"/runs/{R1}/confirm", {"credential": cred(R1, "", ["f1"], "E1", "合格")})
    check(not r["ok"], "空路径不能发布")

    print("4) R1 评估超时保持待确认")
    r = call("POST", f"/runs/{R1}/confirm", {"credential": cred(R1, readable, ["f1"], "E-T", "超时")})
    check(not r["ok"] and r["status"] == "待确认", "评估超时保持待确认")

    print("5) R2 先交齐完整凭证发布，同凭证两次提交同一归档")
    cols = ["age", "income", "balance", "tenure"]
    c2 = cred(R2, readable, cols, "EVAL-R2-OK", "合格", 0.93)
    first = call("POST", f"/runs/{R2}/confirm", {"credential": c2})
    check(first["ok"] and first["confirmation"]["发布状态"] == "已发布", "R2 发布为可预测产物")
    second = call("POST", f"/runs/{R2}/confirm", {"credential": c2})
    check(second["duplicate"], "重复提交判为重复确认")
    check(second["confirmation"]["id"] == first["confirmation"]["id"], "两次回执对应同一发布归档")
    items = call("GET", "/confirmations")
    check(sum(1 for x in items["items"] if x["跑次编号"] == R2) == 1, "R2 只有一条确认记录")

    print("6) 注册入口与预测入口报告同一可用版本与绑定")
    avail = call("GET", f"/versions/{VERSION}/availability")
    check(avail["available"] and avail["来源跑次"] == R2, "可用版本指向 R2")
    check(avail["模型文件"] == readable and avail["入模字段"] == cols, "可用性结果带文件与入模列")
    check(avail["评估回执编号"] == "EVAL-R2-OK", "可用性结果带评估回执")

    print("7) 预测申请：等待分支重新提交直到受理")
    wv = "v8.8.8"
    call("POST", "/runs/accept", {"run_code": "HTTP-W1", "model_version": wv})
    waiting = call("POST", "/predict-applications",
                   {"application_code": "APP-W", "model_version": wv, "data_slice": "s1"})
    check(waiting["受理状态"] == "等待中" and "predict_task" not in waiting, "无产物时申请等待")
    call("POST", "/runs/HTTP-W1/confirm",
         {"credential": cred("HTTP-W1", readable, ["x"], "E-W", "合格")})
    accepted_wait = call("POST", "/predict-applications",
                         {"application_code": "APP-W", "model_version": wv, "data_slice": "s1"})
    check(accepted_wait["受理状态"] == "已受理", "等待申请重新提交后受理")

    print("8) 已发布版本直接受理，重复提交保留原任务")
    acc = call("POST", "/predict-applications",
               {"application_code": "APP-M", "model_version": VERSION, "data_slice": "s2"})
    check(acc["受理状态"] == "已受理", "预测申请直接受理")
    task = acc["predict_task"]
    check(task["绑定模型文件"] == readable and task["绑定入模字段"] == cols, "任务固定 R2 文件与字段")
    check(task["绑定评估回执"]["receipt_id"] == "EVAL-R2-OK" and task["来源跑次"] == R2,
          "任务固定 R2 评估凭证与来源跑次")
    again = call("POST", "/predict-applications",
                 {"application_code": "APP-M", "model_version": VERSION, "data_slice": "s2"})
    check(again["duplicate"] and again["application"]["预测任务编号"] == task["任务编号"],
          "已受理后重复提交保留原任务")

    print("9) 同版本另一跑次补齐发布后，已受理预测绑定不变，详情显示实际采用产物")
    r1_cols = cols + ["extra_flag"]
    pub1 = call("POST", f"/runs/{R1}/confirm",
                {"credential": cred(R1, readable, r1_cols, "EVAL-R1-OK", "合格", 0.88)})
    check(pub1["ok"] and pub1["confirmation"]["id"] != first["confirmation"]["id"],
          "R1 补齐后独立归档发布")
    detail = call("GET", f"/predict-tasks/{task['id']}/detail")
    check(detail["绑定模型文件"] == readable, "详情显示实际模型文件")
    check(detail["绑定入模字段"] == cols, "详情显示 R2 入模字段，未被 R1 覆盖")
    check(detail["绑定评估回执"]["receipt_id"] == "EVAL-R2-OK", "详情显示实际评估回执")
    check(detail["来源跑次"] == R2 and detail["发布确认编号"] == first["confirmation"]["确认编号"],
          "详情保持 R2 来源跑次与原归档")
    avail2 = call("GET", f"/versions/{VERSION}/availability")
    check(avail2["来源跑次"] == R2, "版本可用绑定稳定，不被后来跑次顶替")

    print("10) 跨跑次拼凑文件/评估不能发布")
    call("POST", "/runs/accept", {"run_code": R3, "model_version": VERSION})
    mixed = {"run_id": R3, "model_file": readable, "artifact_run_id": R2,
             "input_columns": cols,
             "evaluation": {"run_id": R2, "receipt_id": "EVAL-R2-OK", "status": "合格", "metrics": {}}}
    r = call("POST", f"/runs/{R3}/confirm", {"credential": mixed})
    check(not r["ok"] and any("跨跑次" in e for e in r["errors"]), "跨跑次材料被拒绝并给出原因")

    print("\n全部 HTTP 核验通过")


if __name__ == "__main__":
    main()
