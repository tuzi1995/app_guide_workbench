<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { state, actions } from '../store'

defineOptions({ name: 'NodeInspector' })

const node = computed(() => {
  const id = state.selectedNodeId
  if (!id) return null
  return (state.graph?.nodes || []).find((n) => n.id === id) || null
})

// 路径里引用了这个节点的路径 —— 截图更新后这些路径都需要重新渲染
const affectedPaths = computed(() => {
  const current = node.value
  if (!current) return []
  return (state.graph?.paths || []).filter((p) => (p.node_ids || []).includes(current.id))
})

// 表单用本地副本承载输入，提交时机放在 blur 上：
// 每个字符都打一次接口既吵又容易打乱光标。
const form = ref({ title: '', action: '', description: '' })
// 当前正在输入的字段：store 侧被外部改写时不要覆盖用户正在输入的内容
const activeField = ref('')

function resetForm() {
  form.value = {
    title: node.value?.title || '',
    action: node.value?.action || '',
    description: node.value?.description || '',
  }
}

// 只在「换节点」时重建表单。
// 注意不能直接 watch node 对象：store 每次写回都会产生新的节点对象，
// 那样会把用户还没提交完的输入冲掉。
watch(
  () => node.value?.id,
  () => {
    resetForm()
    activeField.value = ''
  },
  { immediate: true },
)

// 外部（比如流程图那边改了节点）同步回表单，但不打断正在输入的字段
watch(
  () => {
    const n = node.value
    if (!n) return null
    return {
      title: n.title || '',
      action: n.action || '',
      description: n.description || '',
    }
  },
  (val) => {
    if (!val) return
    for (const key of ['title', 'action', 'description']) {
      if (activeField.value === key) continue
      if (form.value[key] !== val[key]) form.value[key] = val[key]
    }
  },
  { deep: true, immediate: true },
)

/** 失焦时提交单个字段的改动 */
function commit(field) {
  const current = node.value
  if (!current) return
  const value = form.value[field] ?? ''
  if ((current[field] || '') === value) return
  actions.updateNode(current.id, { [field]: value })
}

function onFocus(field) {
  activeField.value = field
}

// 提交完就放开该字段，之后外部同步可以正常回填
function onBlur(field) {
  commit(field)
  if (activeField.value === field) activeField.value = ''
}

const fileInput = ref(null)
const uploading = ref(false)
const previewOpen = ref(false)
const previewActualSize = ref(false)
const previewDialog = ref(null)
const previewTrigger = ref(null)

watch(() => [node.value?.id, node.value?.screenshot], () => {
  previewOpen.value = false
  screenshotDimensions.value = { width: 0, height: 0 }
})

async function openPreview(event) {
  previewTrigger.value = event.currentTarget
  previewActualSize.value = false
  previewOpen.value = true
  await nextTick()
  previewDialog.value?.focus()
}

const screenshotDimensions = ref({ width: 0, height: 0 })

function onPreviewLoad(event) {
  screenshotDimensions.value = {
    width: event.target.naturalWidth,
    height: event.target.naturalHeight,
  }
}

function closePreview() {
  previewOpen.value = false
  previewTrigger.value?.focus()
}

function onPreviewKeydown(event) {
  if (event.key === 'Escape') closePreview()
}

function pickFile() {
  fileInput.value?.click()
}

async function onFilePicked(event) {
  const input = event.target
  const file = input.files && input.files[0]
  const current = node.value
  if (!file || !current) {
    input.value = ''
    return
  }
  uploading.value = true
  try {
    const res = await actions.uploadScreenshot(current.id, file)
    const data = res?.data || {}
    const affected = data.affected_paths || []
    if (data.changed) {
      // 后端已经沿连线自动重渲染了受影响的路径，这里把结果说清楚
      const rendered = affected.filter((p) => p.rendered).length
      actions.toast(
        'success',
        rendered
          ? `检测到页面改版，已自动重新生成 ${rendered} 条路径的长图和文字步骤文档`
          : '检测到页面改版',
      )
    } else {
      actions.toast('success', '截图已替换')
    }
  } catch (err) {
    actions.toast('error', `截图上传失败：${err?.message || err}`)
  } finally {
    uploading.value = false
    input.value = '' // 重置以便再次选择同一文件
  }
}

function onDelete() {
  const current = node.value
  if (!current) return
  const title = current.title || '未命名节点'
  if (!window.confirm(`确认删除节点「${title}」？相关连线会一并移除。`)) return
  actions.removeNode(current.id)
  actions.toast('success', '节点已删除')
}

/** 截图预览地址：兼容直接存 URL 或只存文件名的情况 */
function shotUrl(name) {
  if (!name) return ''
  if (/^(https?:|data:|blob:)/i.test(name)) return name
  if (name.startsWith('/media/')) return name
  return `/media/${name}`
}
</script>

<template>
  <section class="node-inspector">
    <header class="ni-head">
      <h2>节点属性</h2>
      <span v-if="node" class="ni-id">{{ node.id }}</span>
    </header>

    <p v-if="!node" class="ni-empty">在画布上点一个节点，这里会显示它的属性</p>

    <template v-else>
      <div class="ni-field">
        <label for="ni-title">标题</label>
        <input
          id="ni-title"
          v-model="form.title"
          type="text"
          placeholder="短标题，如：点击「我的」"
          @focus="onFocus('title')"
          @blur="onBlur('title')"
        />
        <p class="ni-hint">用于画布节点与步骤列表的简短名称</p>
      </div>

      <div class="ni-field">
        <label for="ni-action">操作说明</label>
        <textarea
          id="ni-action"
          v-model="form.action"
          rows="4"
          placeholder="详细动作，会进入长图与知识库文档"
          @focus="onFocus('action')"
          @blur="onBlur('action')"
        ></textarea>
        <p class="ni-hint">会写进长图和知识库文档</p>
      </div>

      <div class="ni-field">
        <label for="ni-desc">备注</label>
        <textarea
          id="ni-desc"
          v-model="form.description"
          rows="2"
          placeholder="内部备注，不进长图"
          @focus="onFocus('description')"
          @blur="onBlur('description')"
        ></textarea>
        <p class="ni-hint">仅供内部参考，不进长图</p>
      </div>

      <div class="ni-field">
        <label>截图</label>
        <div class="ni-shot">
          <button v-if="node.screenshot" class="ni-shot-preview" type="button" aria-label="查看节点原图" @click="openPreview">
            <img :key="node.screenshot" :src="shotUrl(node.screenshot)" :alt="node.title" @load="onPreviewLoad" />
          </button>
          <div v-else class="ni-shot-empty">暂无截图</div>
          <p v-if="node.screenshot && screenshotDimensions.width" class="ni-hint">实际尺寸：{{ screenshotDimensions.width }} × {{ screenshotDimensions.height }} px</p>
          <p v-if="screenshotDimensions.width && screenshotDimensions.width < 480" class="ni-resolution-warning">当前绑定的是低像素图片，放大不会增加细节。请核对是否选中了原始截图。</p>
          <button v-if="node.screenshot" class="btn" type="button" @click="openPreview">查看原图</button>
          <button class="btn" type="button" :disabled="uploading" @click="pickFile">
            {{ uploading ? '上传中…' : '替换截图' }}
          </button>
        </div>
        <input
          ref="fileInput"
          class="hidden-input"
          type="file"
          accept="image/*"
          @change="onFilePicked"
        />
      </div>

      <div v-if="node.changed" class="ni-changed">
        <p class="ni-changed-title">
          该节点截图已更新（差异度 {{ Math.round((node.diff_ratio || 0) * 100) }}%，第
          {{ node.screenshot_version || 1 }} 版）
        </p>
        <p v-if="affectedPaths.length" class="ni-changed-sub">受影响的路径：</p>
        <ul v-if="affectedPaths.length" class="ni-affected">
          <li v-for="p in affectedPaths" :key="p.id">{{ p.title || p.id }}</li>
        </ul>
        <p v-else class="ni-changed-sub">该节点暂未被任何路径引用。</p>
      </div>

      <button class="btn btn-danger" type="button" @click="onDelete">删除节点</button>
    </template>
  </section>
  <Teleport to="body">
    <div v-if="previewOpen && node?.screenshot" class="ni-preview-overlay" @click.self="closePreview" @keydown="onPreviewKeydown">
      <section ref="previewDialog" class="ni-preview-dialog" role="dialog" aria-modal="true" aria-label="节点原图预览" tabindex="-1">
        <header class="ni-preview-toolbar">
          <div><strong>{{ node.title || node.id }} · 节点原图</strong><p>{{ screenshotDimensions.width }} × {{ screenshotDimensions.height }} px · {{ node.screenshot }}</p></div>
          <button class="btn" type="button" :aria-pressed="!previewActualSize" @click="previewActualSize = false">适应窗口</button>
          <button class="btn" type="button" :aria-pressed="previewActualSize" @click="previewActualSize = true">100% 原始像素</button>
          <button class="btn" type="button" aria-label="关闭原图预览" @click="closePreview">关闭 ×</button>
        </header>
        <div class="ni-preview-stage" :class="{ 'is-actual': previewActualSize }">
          <img :src="shotUrl(node.screenshot)" :alt="node.title || '节点原图'" @load="onPreviewLoad" />
        </div>
        <p class="ni-preview-tip">直接读取当前节点绑定的原始文件，不使用画布缩略图。100% 模式可滚动核对细节；按 Esc 关闭。</p>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.node-inspector {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 14px;
  text-align: left;
  color: #1f2430;
  font-size: 14px;
  line-height: 1.5;
}

.ni-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.ni-head h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #1f2430;
}

.ni-id {
  font-size: 11px;
  color: #9aa1ab;
  max-width: 45%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ni-empty {
  margin: 0;
  padding: 18px 10px;
  text-align: center;
  color: #9aa1ab;
  font-size: 13px;
  background: #fafbfc;
  border-radius: 10px;
}

.ni-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.ni-field label {
  font-size: 12px;
  font-weight: 600;
  color: #4a5160;
}

.ni-field input[type='text'],
.ni-field textarea {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #dfe3e8;
  border-radius: 8px;
  padding: 7px 9px;
  font-size: 13px;
  font-family: inherit;
  color: #1f2430;
  background: #fff;
  resize: vertical;
}

.ni-field input[type='text']:focus,
.ni-field textarea:focus {
  outline: none;
  border-color: #2b6cff;
  box-shadow: 0 0 0 3px rgba(43, 108, 255, 0.12);
}

.ni-hint {
  margin: 0;
  font-size: 11px;
  color: #9aa1ab;
}

.ni-shot {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.ni-shot img {
  width: 84px;
  height: 112px;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid #e5e8ec;
  background: #f7f8fa;
}

.ni-shot-empty {
  width: 84px;
  height: 112px;
  border-radius: 8px;
  border: 1px dashed #c9d0da;
  background: #fafbfc;
  color: #9aa1ab;
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
}

.hidden-input {
  display: none;
}

.btn {
  border: 1px solid #dfe3e8;
  background: #fff;
  color: #1f2430;
  border-radius: 8px;
  padding: 7px 12px;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}

.btn:hover:not(:disabled) {
  border-color: #2b6cff;
  color: #2b6cff;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-danger {
  border-color: #f0bcbc;
  color: #e5484d;
  background: #fff;
  align-self: flex-start;
}

.btn-danger:hover {
  background: #e5484d;
  border-color: #e5484d;
  color: #fff;
}

.ni-changed {
  border: 1px solid #f3c6c6;
  background: #fdf2f2;
  border-radius: 10px;
  padding: 10px 12px;
}

.ni-changed-title {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: #e5484d;
}

.ni-changed-sub {
  margin: 6px 0 0;
  font-size: 12px;
  color: #8a4a4c;
}

.ni-affected {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12px;
  color: #8a4a4c;
}
.ni-shot-preview { padding: 0; border: 0; background: transparent; cursor: zoom-in; }
.ni-resolution-warning { margin: 0; font-size: 12px; color: #a65b00; }
.ni-preview-overlay { position: fixed; inset: 0; z-index: 10000; background: #101827bb; padding: 24px; display: flex; align-items: center; justify-content: center; }
.ni-preview-dialog { width: min(1400px, 100%); height: 100%; display: flex; flex-direction: column; background: #fff; border-radius: 12px; overflow: hidden; color: #1f2430; outline: none; }
.ni-preview-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 12px 16px; border-bottom: 1px solid #e5e7eb; }
.ni-preview-toolbar > div { flex: 1; min-width: 180px; }
.ni-preview-toolbar p { margin: 4px 0 0; font-size: 12px; color: #657080; overflow-wrap: anywhere; }
.ni-preview-toolbar [aria-pressed='true'] { background: #eaf1ff; border-color: #2b6cff; color: #2b6cff; }
.ni-preview-stage { flex: 1; min-height: 0; overflow: auto; padding: 16px; background: #edf0f4; display: flex; align-items: center; justify-content: center; }
.ni-preview-stage img { display: block; width: auto; height: auto; max-width: 100%; max-height: 100%; object-fit: contain; flex-shrink: 0; }
.ni-preview-stage.is-actual { display: block; }
.ni-preview-stage.is-actual img { max-width: none; max-height: none; margin: 0 auto; }
.ni-preview-tip { padding: 8px 16px; margin: 0; font-size: 12px; color: #657080; }
</style>
