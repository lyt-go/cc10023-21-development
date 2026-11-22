/** 模型发布确认与绑定预测接口：与后端 /api/model-release 路由一一对应。 */
import { request } from './client'

export type EvaluationReceipt = {
  run_id: string
  receipt_id: string
  status: string
  metrics?: Record<string, number>
}

export type ReleaseConfirmation = {
  id: number
  status: string
  确认编号: string
  跑次编号: string
  模型版本: string
  模型文件: string
  入模字段: string[]
  评估回执编号: string
  评估回执: EvaluationReceipt
  凭证指纹: string
  发布状态: string
}

export type TrainingRun = {
  id: number
  status: string
  跑次编号: string
  模型版本: string
  受理序号: number
  评估状态: string
  模型文件: string
  入模字段: string[]
  发布确认编号: number | null
}

export type VersionAvailability = {
  ok: boolean
  available: boolean
  模型版本: string
  状态: string
  发布确认编号?: string
  来源跑次?: string
  模型文件?: string
  入模字段?: string[]
  评估回执编号?: string
  评估回执?: EvaluationReceipt
  待确认跑次?: string[]
  message: string
}

export type BoundPredictTask = {
  id: number
  任务编号: string
  模型版本: string
  数据切片: string
  预测状态: string
  绑定模型文件: string
  绑定入模字段: string[]
  绑定评估回执: EvaluationReceipt
  评估回执编号: string
  来源跑次: string
  发布确认编号: string
}

export type PredictionApplication = {
  id: number
  申请编号: string
  模型版本: string
  数据切片: string
  受理状态: string
  预测任务编号: string | null
  预测任务ID?: number
}

export type ConfirmResult = {
  ok: boolean
  duplicate?: boolean
  status?: string
  message: string
  errors?: string[]
  confirmation?: ReleaseConfirmation
  run_id?: string
}

async function postJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return (await response.json()) as T
}

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

export function acceptTrainingRun(runCode: string, modelVersion: string) {
  return postJson<{ ok: boolean; duplicate: boolean; message: string; run: TrainingRun }>(
    '/api/model-release/runs/accept',
    { run_code: runCode, model_version: modelVersion },
  )
}

export function confirmRelease(runCode: string, credential: Record<string, unknown>) {
  return postJson<ConfirmResult>(
    `/api/model-release/runs/${encodeURIComponent(runCode)}/confirm`,
    { credential },
  )
}

export function listConfirmations() {
  return getJson<{ ok: boolean; total: number; items: ReleaseConfirmation[] }>(
    '/api/model-release/confirmations',
  )
}

export function getVersionAvailability(modelVersion: string) {
  return getJson<VersionAvailability>(
    `/api/model-release/versions/${encodeURIComponent(modelVersion)}/availability`,
  )
}

export function registerPredictionApplication(
  applicationCode: string,
  modelVersion: string,
  dataSlice: string,
) {
  return postJson<{
    ok: boolean
    duplicate?: boolean
    受理状态: string
    message: string
    application: PredictionApplication
    availability: VersionAvailability
    predict_task?: BoundPredictTask
  }>('/api/model-release/predict-applications', {
    application_code: applicationCode,
    model_version: modelVersion,
    data_slice: dataSlice,
  })
}

export function getPredictionApplication(applicationCode: string) {
  return getJson<{ ok: boolean; application: PredictionApplication; availability: VersionAvailability }>(
    `/api/model-release/predict-applications/${encodeURIComponent(applicationCode)}`,
  )
}

export function getPredictionTaskDetail(taskId: number) {
  return getJson<{
    ok: boolean
    message?: string
    task_id: number
    任务编号: string
    预测状态: string
    模型版本: string
    数据切片: string
    来源跑次: string
    发布确认编号: string
    绑定模型文件: string
    绑定入模字段: string[]
    绑定评估回执: EvaluationReceipt
    评估回执编号: string
  }>(`/api/model-release/predict-tasks/${taskId}/detail`)
}
