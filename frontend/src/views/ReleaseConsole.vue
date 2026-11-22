<template>
  <section class="page" data-module="model_release">
    <header class="page-head">
      <div>
        <h2>模型发布确认</h2>
        <p class="page-desc">
          按实际训练产物确认发布：模型文件实际可读、入模列齐备、同一跑次合格评估同时满足，
          才绑定为可预测模型产物；路径存在但不可读、评估超时一律保持待确认。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="refresh-confirmations" @click="loadConfirmations">
          刷新发布归档
        </button>
      </div>
    </header>

    <div class="release-grid">
      <!-- 跑次受理 -->
      <form class="panel" @submit.prevent="submitAccept">
        <h3>1. 受理训练跑次</h3>
        <label class="create-field"><span>跑次编号</span>
          <input v-model="acceptForm.runCode" data-testid="accept-run-code" placeholder="如 RUN-R1" required />
        </label>
        <label class="create-field"><span>模型版本</span>
          <input v-model="acceptForm.modelVersion" data-testid="accept-model-version" placeholder="如 v2.3.0" required />
        </label>
        <button class="btn primary" type="submit" data-testid="submit-accept">受理跑次</button>
      </form>

      <!-- 发布确认 -->
      <form class="panel" @submit.prevent="submitConfirm">
        <h3>2. 凭完成凭证确认发布</h3>
        <label class="create-field"><span>跑次编号</span>
          <input v-model="confirmForm.runCode" data-testid="confirm-run-code" required />
        </label>
        <label class="create-field"><span>模型文件路径（服务端实际读取校验）</span>
          <input v-model="confirmForm.modelFile" data-testid="confirm-model-file" required />
        </label>
        <label class="create-field"><span>入模字段（逗号分隔）</span>
          <input v-model="confirmForm.inputColumns" data-testid="confirm-columns" placeholder="age,income" required />
        </label>
        <label class="create-field"><span>评估回执编号</span>
          <input v-model="confirmForm.receiptId" data-testid="confirm-receipt" required />
        </label>
        <label class="create-field"><span>评估结论</span>
          <select v-model="confirmForm.evalStatus" data-testid="confirm-eval-status">
            <option value="合格">合格</option>
            <option value="超时">超时（保持待确认）</option>
          </select>
        </label>
        <button class="btn primary" type="submit" data-testid="submit-confirm">提交发布确认</button>
        <p class="hint">同一份完成凭证重复提交只记一次确认，返回原发布归档。</p>
      </form>

      <!-- 版本可用性 -->
      <form class="panel" @submit.prevent="submitAvailability">
        <h3>3. 注册入口：查询版本可用性</h3>
        <label class="create-field"><span>模型版本</span>
          <input v-model="availabilityVersion" data-testid="availability-version" required />
        </label>
        <button class="btn primary" type="submit" data-testid="submit-availability">查询能否开始预测</button>
        <div v-if="availability" class="result-box" :class="availability.available ? 'ok' : 'pending'">
          <p><strong>{{ availability.available ? '可预测（已发布）' : '不可预测（' + availability.状态 + '）' }}</strong></p>
          <p>{{ availability.message }}</p>
          <template v-if="availability.available">
            <p>来源跑次：{{ availability.来源跑次 }} ｜ 发布归档：{{ availability.发布确认编号 }}</p>
            <p>模型文件：{{ availability.模型文件 }}</p>
            <p>入模字段：{{ (availability.入模字段 ?? []).join(', ') }}</p>
            <p>评估回执：{{ availability.评估回执编号 }}</p>
          </template>
          <p v-else>待确认跑次：{{ (availability.待确认跑次 ?? []).join(', ') }}</p>
        </div>
      </form>

      <!-- 预测申请 -->
      <form class="panel" @submit.prevent="submitApplication">
        <h3>4. 预测入口：登记预测申请</h3>
        <label class="create-field"><span>申请编号</span>
          <input v-model="applicationForm.applicationCode" data-testid="app-code" required />
        </label>
        <label class="create-field"><span>模型版本</span>
          <input v-model="applicationForm.modelVersion" data-testid="app-version" required />
        </label>
        <label class="create-field"><span>数据切片</span>
          <input v-model="applicationForm.dataSlice" data-testid="app-slice" required />
        </label>
        <button class="btn primary" type="submit" data-testid="submit-app">登记/重提预测申请</button>
        <p class="hint">等待中的申请在版本发布后重新提交即受理；已受理申请保留原任务与固定绑定。</p>
        <div v-if="applicationResult" class="result-box" :class="applicationResult.受理状态 === '已受理' ? 'ok' : 'pending'">
          <p><strong>受理状态：{{ applicationResult.受理状态 }}</strong></p>
          <p>{{ applicationResult.message }}</p>
          <template v-if="applicationResult.predict_task">
            <p>预测任务：{{ applicationResult.predict_task.任务编号 }} ｜ 来源跑次：{{ applicationResult.predict_task.来源跑次 }}</p>
            <p>固定文件：{{ applicationResult.predict_task.绑定模型文件 }}</p>
            <p>固定入模列：{{ applicationResult.predict_task.绑定入模字段.join(', ') }}</p>
            <p>评估回执：{{ applicationResult.predict_task.评估回执编号 }}</p>
          </template>
        </div>
      </form>

      <!-- 预测详情 -->
      <form class="panel" @submit.prevent="submitDetail">
        <h3>5. 查询预测详情（实际采用的绑定）</h3>
        <label class="create-field"><span>预测任务 ID</span>
          <input v-model.number="detailTaskId" data-testid="detail-task-id" type="number" min="1" required />
        </label>
        <button class="btn primary" type="submit" data-testid="submit-detail">查询详情</button>
        <div v-if="taskDetail?.ok" class="result-box ok">
          <p>任务 {{ taskDetail.任务编号 }}（{{ taskDetail.预测状态 }}）</p>
          <p>来源跑次：{{ taskDetail.来源跑次 }} ｜ 发布归档：{{ taskDetail.发布确认编号 }}</p>
          <p>模型文件：{{ taskDetail.绑定模型文件 }}</p>
          <p>入模字段：{{ taskDetail.绑定入模字段.join(', ') }}</p>
          <p>评估回执：{{ taskDetail.评估回执编号 }}（{{ taskDetail.绑定评估回执.status }}）</p>
        </div>
      </form>
    </div>

    <h3>发布归档（可预测模型产物）</h3>
    <table class="data-table" data-testid="confirmation-table">
      <thead>
        <tr>
          <th>确认编号</th><th>跑次</th><th>模型版本</th><th>模型文件</th>
          <th>入模字段</th><th>评估回执</th><th>发布状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in confirmations" :key="item.id" :data-testid="`confirmation-row-${item.id}`">
          <td>{{ item.确认编号 }}</td>
          <td>{{ item.跑次编号 }}</td>
          <td>{{ item.模型版本 }}</td>
          <td>{{ item.模型文件 }}</td>
          <td>{{ item.入模字段.join(', ') }}</td>
          <td>{{ item.评估回执编号 }}</td>
          <td>{{ item.发布状态 }}</td>
        </tr>
        <tr v-if="!confirmations.length">
          <td colspan="7" class="empty-state">暂无发布归档，先完成一次发布确认</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ confirmations.length }} 条发布确认记录</span>
      <span v-if="errorMessage" class="error-text" data-testid="release-error">{{ errorMessage }}</span>
      <span v-else-if="infoMessage" class="info-text" data-testid="release-info">{{ infoMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  acceptTrainingRun,
  confirmRelease,
  getPredictionTaskDetail,
  getVersionAvailability,
  listConfirmations,
  registerPredictionApplication,
} from '@/api/model-release'
import type {
  BoundPredictTask,
  ConfirmResult,
  ReleaseConfirmation,
  VersionAvailability,
} from '@/api/model-release'

const acceptForm = ref({ runCode: '', modelVersion: '' })
const confirmForm = ref({
  runCode: '',
  modelFile: '',
  inputColumns: '',
  receiptId: '',
  evalStatus: '合格',
})
const availabilityVersion = ref('')
const applicationForm = ref({ applicationCode: '', modelVersion: '', dataSlice: 'full_dataset_slice_001' })
const detailTaskId = ref<number | null>(null)

const confirmations = ref<ReleaseConfirmation[]>([])
const availability = ref<VersionAvailability | null>(null)
const applicationResult = ref<{
  受理状态: string
  message: string
  predict_task?: BoundPredictTask
} | null>(null)
const taskDetail = ref<Awaited<ReturnType<typeof getPredictionTaskDetail>> | null>(null)
const errorMessage = ref('')
const infoMessage = ref('')

function flashError(message: string) {
  errorMessage.value = message
  infoMessage.value = ''
}

function flashInfo(message: string) {
  infoMessage.value = message
  errorMessage.value = ''
}

async function loadConfirmations() {
  try {
    const payload = await listConfirmations()
    confirmations.value = payload.items
  } catch (error) {
    flashError(error instanceof Error ? error.message : '发布归档读取失败')
  }
}

async function submitAccept() {
  try {
    const result = await acceptTrainingRun(acceptForm.value.runCode, acceptForm.value.modelVersion)
    flashInfo(result.message)
  } catch (error) {
    flashError(error instanceof Error ? error.message : '跑次受理失败')
  }
}

async function submitConfirm() {
  const columns = confirmForm.value.inputColumns
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
  const credential = {
    run_id: confirmForm.value.runCode,
    model_file: confirmForm.value.modelFile,
    input_columns: columns,
    evaluation: {
      run_id: confirmForm.value.runCode,
      receipt_id: confirmForm.value.receiptId,
      status: confirmForm.value.evalStatus,
    },
  }
  try {
    const result: ConfirmResult = await confirmRelease(confirmForm.value.runCode, credential)
    if (result.ok) {
      flashInfo(result.duplicate ? `${result.message}（归档 #${result.confirmation?.确认编号}）` : result.message)
    } else {
      flashError(`${result.message}${result.errors?.length ? '：' + result.errors.join('；') : ''}`)
    }
    await loadConfirmations()
  } catch (error) {
    flashError(error instanceof Error ? error.message : '发布确认失败')
  }
}

async function submitAvailability() {
  try {
    availability.value = await getVersionAvailability(availabilityVersion.value)
    flashInfo(availability.value.message)
  } catch (error) {
    flashError(error instanceof Error ? error.message : '版本可用性查询失败')
  }
}

async function submitApplication() {
  try {
    const result = await registerPredictionApplication(
      applicationForm.value.applicationCode,
      applicationForm.value.modelVersion,
      applicationForm.value.dataSlice,
    )
    applicationResult.value = result
    flashInfo(result.message)
  } catch (error) {
    flashError(error instanceof Error ? error.message : '预测申请失败')
  }
}

async function submitDetail() {
  if (!detailTaskId.value) return
  try {
    taskDetail.value = await getPredictionTaskDetail(detailTaskId.value)
    if (!taskDetail.value.ok) flashError(taskDetail.value.message ?? '预测详情不存在')
  } catch (error) {
    flashError(error instanceof Error ? error.message : '预测详情查询失败')
  }
}

onMounted(loadConfirmations)
</script>

<style scoped>
.release-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
  margin: 0;
}
.panel h3 {
  margin: 0 0 10px;
  font-size: 14px;
}
.panel select {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px;
}
.hint {
  font-size: 12px;
  color: var(--muted);
  margin: 8px 0 0;
}
.result-box {
  margin-top: 10px;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 13px;
}
.result-box.ok {
  background: #ecfdf3;
  border: 1px solid #abefc6;
}
.result-box.pending {
  background: #fffaeb;
  border: 1px solid #fedf89;
}
.result-box p {
  margin: 4px 0;
  word-break: break-all;
}
.info-text {
  color: #175cd3;
}
</style>
