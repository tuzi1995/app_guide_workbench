<script setup>
import { computed, ref, watch } from 'vue'
import { state, actions } from '../store'
import AnnotationEditor from './AnnotationEditor.vue'

defineOptions({ name: 'PathPanel' })

const paths = computed(() => state.graph?.paths || [])
const nodes = computed(() => state.graph?.nodes || [])

const selectedPath = computed(() => {
  const id = state.selectedPathId
  if (!id) return null
  return paths.value.find((p) => p.id === id) || null
})

function nodeById(id) {
  return nodes.value.find((n) => n.id === id) || null
}

function shotUrl(name) {
  if (!name) return ''
  if (/^(https?:|data:|blob:)/i.test(name)) return name
  if (name.startsWith('/media/')) return name
  return `/media/${name}`
}

/* ------------------------------------------------------------------ */
/* 新建路径                                                             */
/* ------------------------------------------------------------------ */

const showCreate = ref(false)
const createTitle = ref('')
const createStartNode = ref('')
const creating = ref(false)
const deriveOpen = ref(false)
const deriveSource = ref('')
const deriveTarget = ref('')
const derivedPaths = ref([])
const selectedDerivedPath = ref(0)
const deriving = ref(false)
const applyingDerivedPath = ref(false)

// 打开展开表单时，默认把当前选中的节点作为起始步骤
watch(showCreate, (open) => {
  if (!open) return
  createStartNode.value = state.selectedNodeId || ''
})

async function submitCreate() {
  const title = createTitle.value.trim()
  if (!title) {
    actions.toast('error', '请填写路径标题')
    return
  }
  creating.value = true
  try {
    await actions.addPath({
      title,
      node_ids: createStartNode.value ? [createStartNode.value] : [],
      question: '',
      product_name: '',
      product_category_name: '',
    })
    actions.toast('success', `路径「${title}」已创建`)
    showCreate.value = false
    createTitle.value = ''
    createStartNode.value = ''
  } catch (err) {
    actions.toast('error', `创建失败：${err?.message || err}`)
  } finally {
    creating.value = false
  }
}

/* ------------------------------------------------------------------ */
/* 路径列表                                                             */
/* ------------------------------------------------------------------ */

/** 徽标优先级：截图更新 > 已入库 > 未渲染 */
function badgeOf(path) {
  if (!path.render_version) return { text: '未生成', type: 'muted' }
  if (path.stale) return { text: '内容已修改，需重新生成', type: 'warn' }
  if (path.kb_published_at && path.kb_published_version !== path.render_version) return { text: '已生成，待同步知识库', type: 'warn' }
  if (path.kb_published_at) return { text: '已入库', type: 'ok' }
  return { text: '已生成', type: 'ok' }
}

function selectPath(path) {
  actions.selectPath(path.id)
}

function annotationsFor(path, nodeId) {
  const value = path?.annotations?.[nodeId]
  return Array.isArray(value) ? value : []
}

const annotationTarget = ref(null)
const annotationSaving = ref(false)

function addAnnotation(path, nodeId) {
  const node = nodeById(nodeId)
  if (!node?.screenshot) return actions.toast('error', '该节点还没有截图')
  annotationTarget.value = { pathId: path.id, nodeId, screenshot: node.screenshot, title: `${path.title} / ${node.title || nodeId}`, marks: JSON.parse(JSON.stringify(annotationsFor(path, nodeId))) }
}

async function saveAnnotations(marks) {
  const target = annotationTarget.value
  const path = paths.value.find(p => p.id === target?.pathId)
  if (!path || nodeById(target.nodeId)?.screenshot !== target.screenshot) {
    actions.toast('error', '路径或截图已变化，请关闭后重新标注')
    return
  }
  annotationSaving.value = true
  try {
    const annotations = { ...(path.annotations || {}), [target.nodeId]: JSON.parse(JSON.stringify(marks)) }
    await actions.updatePath(path.id, { annotations, annotation_invalidated_nodes: (path.annotation_invalidated_nodes || []).filter(id => id !== target.nodeId) })
    annotationTarget.value = null
    renderResult.value = null
    showResult.value = false
    actions.toast('success', '标注已保存，仅当前路径生效；请重渲染长图查看结果')
  } catch (err) {
    actions.toast('error', `保存失败：${err.message}`)
  } finally {
    annotationSaving.value = false
  }
}

async function clearAnnotations(path, nodeId) {
  const annotations = { ...(path.annotations || {}) }
  delete annotations[nodeId]
  await actions.updatePath(path.id, { annotations })
  actions.toast('success', '该步骤标注已清除')
}

async function removePath(path) {
  if (!window.confirm(`确认删除路径「${path.title || path.id}」？`)) return
  try {
    await actions.removePath(path.id)
    actions.toast('success', '路径已删除')
  } catch (err) {
    actions.toast('error', `删除失败：${err?.message || err}`)
  }
}

/* ------------------------------------------------------------------ */
/* 步骤管理                                                             */
/* ------------------------------------------------------------------ */

function removeStep(path, nodeId) {
  const next = (path.node_ids || []).filter((id) => id !== nodeId)
  actions.updatePath(path.id, { node_ids: next })
}

async function addStep(path, nodeId) {
  const next = [...(path.node_ids || []), nodeId]
  const previous = next.at(-2)
  const hasEdge = (state.graph.edges || []).some((edge) => edge.source === previous && edge.target === nodeId)
  if (previous && !hasEdge && !window.confirm(`添加该步骤时会同时创建「${nodeById(previous)?.title || previous} → ${nodeById(nodeId)?.title || nodeId}」连线，继续吗？`)) return

  let createdEdge
  try {
    if (previous && !hasEdge) {
      createdEdge = await actions.createEdge({ source: previous, target: nodeId, label: '' })
    }
    await actions.updatePath(path.id, { node_ids: next })
  } catch (err) {
    if (createdEdge?.id) {
      try { await actions.removeEdge(createdEdge.id) } catch (_) { /* Keep the original error visible. */ }
    }
    actions.toast('error', `添加步骤失败：${err?.message || err}`)
  }
}

async function findDerivedPaths() {
  if (!deriveSource.value || !deriveTarget.value) {
    actions.toast('error', '请选择起点和终点')
    return
  }
  deriving.value = true
  try {
    derivedPaths.value = await actions.derivePaths(deriveSource.value, deriveTarget.value)
    selectedDerivedPath.value = 0
    if (!derivedPaths.value.length) actions.toast('warn', '这两个节点之间没有连通路径')
  } catch (err) {
    actions.toast('error', `推导失败：${err?.message || err}`)
  } finally {
    deriving.value = false
  }
}

async function applyDerivedPath(path) {
  const chain = path?.node_ids || []
  if (!selectedPath.value || !chain.length) return
  if ((selectedPath.value.node_ids || []).length && !window.confirm('将用这条连线路径替换当前步骤列表，继续吗？')) return
  applyingDerivedPath.value = true
  try {
    await actions.updatePath(selectedPath.value.id, { node_ids: chain })
    deriveOpen.value = false
    derivedPaths.value = []
    actions.toast('success', '已根据连线更新步骤')
  } catch (err) {
    actions.toast('error', `更新步骤失败：${err?.message || err}`)
  } finally {
    applyingDerivedPath.value = false
  }
}

// 可添加的节点 = 画布上所有还没进这条路径的节点
const addableNodes = computed(() => {
  const path = selectedPath.value
  if (!path) return []
  const used = new Set(path.node_ids || [])
  return nodes.value.filter((n) => !used.has(n.id))
})

/* ------------------------------------------------------------------ */
/* 路径字段（推库信息）                                                  */
/* ------------------------------------------------------------------ */

const categories = computed(() => (state.models || []).map((m) => m.category).filter(Boolean))

const modelsOfCategory = computed(() => {
  const cat = fieldForm.value.product_category_name
  if (!cat) return []
  const entry = (state.models || []).find((m) => m.category === cat)
  return entry?.models || []
})

// 字段同样是「本地编辑 + 失焦提交」，避免每敲一个字符打一次接口
const fieldForm = ref({ title: '', question: '', product_category_name: '', product_name: '', kb_wiki_id: '' })
const activeField = ref('')

function resetFieldForm() {
  const path = selectedPath.value
  fieldForm.value = {
    title: path?.title || '',
    question: path?.question || '',
    product_category_name: path?.product_category_name || '',
    product_name: path?.product_name || '',
    // 知识库ID 默认跟随路径 id
    kb_wiki_id: path?.kb_wiki_id || path?.id || '',
  }
}

// 只在「换路径」时重建表单。直接 watch 路径对象会被 store 的每次写回触发，
// 冲掉用户还没提交的输入。
watch(
  () => selectedPath.value?.id,
  () => {
    resetFieldForm()
    activeField.value = ''
  },
  { immediate: true },
)

watch(
  () => {
    const p = selectedPath.value
    if (!p) return null
    return {
      title: p.title || '',
      question: p.question || '',
      product_category_name: p.product_category_name || '',
      product_name: p.product_name || '',
      kb_wiki_id: p.kb_wiki_id || p.id || '',
    }
  },
  (val) => {
    if (!val) return
    for (const key of Object.keys(fieldForm.value)) {
      if (activeField.value === key) continue
      if (fieldForm.value[key] !== val[key]) fieldForm.value[key] = val[key]
    }
  },
  { deep: true },
)

function commitField(field) {
  const path = selectedPath.value
  if (!path) return
  let value = fieldForm.value[field] ?? ''
  if (field === 'title') {
    value = value.trim()
    if (!value) {
      fieldForm.value.title = path.title || path.id
      actions.toast('error', '路径名称不能为空')
      return
    }
  }
  if (field === 'kb_wiki_id' && !value.trim()) value = path.id // 空则回落到路径 id
  if ((path[field] || '') === value) return
  actions.updatePath(path.id, { [field]: value })
    .then(() => {
      if (field === 'title') actions.toast('success', '路径名称已更新')
    })
    .catch((err) => actions.toast('error', `保存失败：${err?.message || err}`))
}

function onFocus(field) {
  activeField.value = field
}

// 提交完就放开该字段，之后外部同步可以正常回填
function onBlur(field) {
  commitField(field)
  if (activeField.value === field) activeField.value = ''
}

// 切换品类后，原型号可能已不在新列表里，清掉避免推库时被 Hub 判为未知型号
function onCategoryChange() {
  const models = modelsOfCategory.value
  if (fieldForm.value.product_name && !models.includes(fieldForm.value.product_name)) {
    fieldForm.value.product_name = ''
    commitField('product_name')
  }
  commitField('product_category_name')
  if (activeField.value === 'product_category_name') activeField.value = ''
}

/* ------------------------------------------------------------------ */
/* 渲染 / 推送                                                          */
/* ------------------------------------------------------------------ */

const rendering = ref(false)
const publishing = ref(false)
const renderResult = ref(null)
const showResult = ref(false)
const publishResult = ref(null)
const previewImage = ref('')
const loadingResult = ref(false)
let selectionEpoch = 0

function resetResult() {
  renderResult.value = null
  showResult.value = false
  previewImage.value = ''
}

watch(() => selectedPath.value?.id, () => {
  selectionEpoch += 1
  publishResult.value = null
  resetResult()
}, { flush: 'sync' })

watch(() => [selectedPath.value?.long_image_url, selectedPath.value?.stale], resetResult, { flush: 'sync' })

async function toggleResult(path) {
  if (showResult.value) {
    showResult.value = false
    return
  }
  const epoch = selectionEpoch
  loadingResult.value = true
  try {
    const result = await actions.exportPath(path.id)
    if (epoch !== selectionEpoch) return
    renderResult.value = extractRenderResult(result)
    showResult.value = true
  } catch (err) {
    actions.toast('error', `查看失败：${err?.message || err}`)
  } finally {
    loadingResult.value = false
  }
}

function openImagePreview(url) {
  previewImage.value = url
}

function closeImagePreview() {
  previewImage.value = ''
}

async function copyImage(url) {
  try {
    const response = await fetch(url)
    const blob = await response.blob()
    if (navigator.clipboard?.write && typeof ClipboardItem !== 'undefined') {
      await navigator.clipboard.write([new ClipboardItem({ [blob.type || 'image/png']: blob })])
      actions.toast('success', '图片已复制，可直接粘贴')
      return
    }
  } catch (err) {
    // 浏览器权限或跨源限制时降级复制链接
  }
  try {
    await navigator.clipboard.writeText(new URL(url, window.location.origin).href)
    actions.toast('success', '图片链接已复制')
  } catch (err) {
    actions.toast('error', '复制失败，请右键图片另存或复制')
  }
}

/** 从不同形状的返回值里挖出渲染产物（store action 可能直接返回，也可能写进 state） */
function extractRenderResult(value) {
  if (!value) return null
  const payload = value && typeof value === 'object' && value.data && typeof value.data === 'object'
    ? value.data
    : value
  if (!payload || typeof payload !== 'object') return null
  return {
    markdown: payload.markdown || payload.md || payload.content || '',
    longImageUrl:
      payload.long_image_url || payload.longImageUrl || payload.long_image_path || payload.longImagePath || '',
    steps: Array.isArray(payload.steps) ? payload.steps : [],
  }
}

async function doRender(path) {
  const epoch = selectionEpoch
  rendering.value = true
  publishResult.value = null
  try {
    const ret = await actions.renderPath(path.id)
    if (epoch !== selectionEpoch) return
    // action 的返回值和 state 里缓存的产物都试一遍，谁先拿到用谁
    const fromAction = extractRenderResult(ret)
    const fromState = extractRenderResult(state.renderResult || state.render || state.lastRender)
    const fromPath = extractRenderResult(path)
    renderResult.value = fromAction || fromState || fromPath
    showResult.value = true
    actions.toast('success', '长图与文档已生成')
  } catch (err) {
    actions.toast('error', `渲染失败：${err?.message || err}`)
  } finally {
    rendering.value = false
  }
}

/** 在一堆可能的字段名里找第一条非空文本，Hub 的中文报错原文要从这里透出来 */
function pickText(obj) {
  if (!obj) return ''
  if (typeof obj === 'string') return obj
  const keys = ['message', 'msg', 'error', 'detail', 'reason', 'text', 'result']
  for (const k of keys) {
    const v = obj[k]
    if (typeof v === 'string' && v.trim()) return v
  }
  return ''
}

function pickEntryId(obj) {
  if (!obj || typeof obj !== 'object') return ''
  const keys = ['entry_id', 'entryId', 'wiki_id', 'wikiId', 'entry', 'id']
  for (const k of keys) {
    const v = obj[k]
    if (typeof v === 'string' && v.trim()) return v
    if (typeof v === 'number') return String(v)
  }
  return ''
}

const ERROR_PATTERN = /未知型号|失败|错误|不存在|无权限|超时|异常/

/** 把 publish 的返回值归一成 { ok, text, entryId }，text 保持原文不做翻译 */
function normalizePublish(raw) {
  if (raw == null) return null
  if (typeof raw === 'string') {
    return { ok: !ERROR_PATTERN.test(raw), text: raw, entryId: '' }
  }
  const payload = raw && typeof raw === 'object' && raw.data && typeof raw.data === 'object' ? raw.data : raw
  let ok
  if (typeof payload.success === 'boolean') ok = payload.success
  else if (typeof payload.ok === 'boolean') ok = payload.ok
  else if (typeof raw.success === 'boolean') ok = raw.success
  else {
    const text = pickText(payload) || pickText(raw)
    ok = !ERROR_PATTERN.test(text)
  }
  const text = pickText(payload) || (payload !== raw ? pickText(raw) : '')
  const entryId = pickEntryId(payload) || (payload !== raw ? pickEntryId(raw) : '')
  return { ok, text, entryId }
}

async function doPublish(path) {
  const wikiId = path.kb_wiki_id || path.id
  if (!window.confirm(`确认推送？将更新知识库条目 ${wikiId}`)) return
  publishing.value = true
  const epoch = selectionEpoch
  publishResult.value = null
  try {
    const ret = await actions.publishPath(path.id)
    if (epoch !== selectionEpoch) return
    const normalized = normalizePublish(ret)
    if (normalized) {
      publishResult.value = normalized
      if (normalized.ok) actions.toast('success', '已推送到知识库')
      // 失败时不额外弹 toast：下面会原样展示 Hub 返回的中文报错原文
    } else {
      publishResult.value = { ok: true, text: '推送成功', entryId: wikiId }
      actions.toast('success', '已推送到知识库')
    }
  } catch (err) {
    // 后端把 Hub 的错误原文透传上来，这里原样展示，不做英文包装
    if (epoch !== selectionEpoch) return
    publishResult.value = { ok: false, text: err?.message || String(err), entryId: '' }
  } finally {
    publishing.value = false
  }
}
</script>

<template>
  <section class="path-panel">
    <header class="pp-head">
      <h2>功能路径</h2>
      <button class="btn btn-primary" type="button" @click="showCreate = !showCreate">
        {{ showCreate ? '取消' : '+ 新建路径' }}
      </button>
    </header>

    <!-- 新建路径表单 -->
    <form v-if="showCreate" class="pp-create" @submit.prevent="submitCreate">
      <label for="pp-new-title">路径标题</label>
      <input
        id="pp-new-title"
        v-model="createTitle"
        type="text"
        placeholder="如：开启自动清扫"
        @focus="onFocus('newTitle')"
      />

      <label for="pp-new-node">起始节点</label>
      <select id="pp-new-node" v-model="createStartNode">
        <option value="">（暂不指定）</option>
        <option v-for="n in nodes" :key="n.id" :value="n.id">{{ n.title || n.id }}</option>
      </select>

      <div class="pp-create-actions">
        <button class="btn btn-primary" type="submit" :disabled="creating">
          {{ creating ? '创建中…' : '创建' }}
        </button>
      </div>
    </form>

    <p v-if="!paths.length" class="pp-empty">还没有功能路径，点「+ 新建路径」开始。</p>

    <ul v-else class="pp-list">
      <li
        v-for="path in paths"
        :key="path.id"
        class="pp-item"
        :class="{ 'is-active': path.id === state.selectedPathId }"
        @click="selectPath(path)"
      >
        <div class="pp-item-main">
          <span class="pp-item-title">{{ path.title || path.id }}</span>
          <span class="pp-item-steps">{{ (path.node_ids || []).length }} 步</span>
        </div>
        <span class="pp-badge" :class="`is-${badgeOf(path).type}`">{{ badgeOf(path).text }}</span>
        <button
          class="pp-del"
          type="button"
          title="删除路径"
          @click.stop="removePath(path)"
        >
          ×
        </button>
      </li>
    </ul>

    <!-- 选中路径的详情 -->
    <div v-if="selectedPath" class="pp-detail">
      <div class="pp-field">
        <label for="pp-title">路径名称</label>
        <input
          id="pp-title"
          v-model="fieldForm.title"
          class="pp-title-input"
          type="text"
          placeholder="请输入路径名称"
          @focus="onFocus('title')"
          @blur="onBlur('title')"
          @keydown.enter.prevent="$event.currentTarget.blur()"
        />
      </div>

      <!-- 步骤列表 -->
      <section class="pp-block">
        <h4 class="pp-block-title">步骤（{{ (selectedPath.node_ids || []).length }}）</h4>
        <button class="btn pp-derive-toggle" type="button" @click="deriveOpen = !deriveOpen; derivedPaths = []">
          {{ deriveOpen ? '收起连线推导' : '根据连线生成步骤' }}
        </button>
        <div v-if="deriveOpen" class="pp-derive">
          <label for="pp-derive-source">起点</label>
          <select id="pp-derive-source" v-model="deriveSource">
            <option value="">选择起点</option>
            <option v-for="n in nodes" :key="n.id" :value="n.id">{{ n.title || n.id }}</option>
          </select>
          <label for="pp-derive-target">终点</label>
          <select id="pp-derive-target" v-model="deriveTarget">
            <option value="">选择终点</option>
            <option v-for="n in nodes" :key="n.id" :value="n.id">{{ n.title || n.id }}</option>
          </select>
          <button class="btn" type="button" :disabled="deriving" @click="findDerivedPaths">
            {{ deriving ? '查找中…' : '查找路径' }}
          </button>
          <ol v-if="derivedPaths.length" class="pp-derived-list">
            <li v-for="(candidate, index) in derivedPaths" :key="candidate.node_ids.join(':')">
              <label>
                <input v-model.number="selectedDerivedPath" type="radio" :value="index" />
                {{ candidate.node_ids.map((id) => nodeById(id)?.title || id).join(' → ') }}
              </label>
            </li>
          </ol>
          <button v-if="derivedPaths.length" class="btn btn-primary" type="button" :disabled="applyingDerivedPath" @click="applyDerivedPath(derivedPaths[selectedDerivedPath])">
            {{ applyingDerivedPath ? '更新中…' : '替换当前步骤' }}
          </button>
        </div>
        <p v-if="!(selectedPath.node_ids || []).length" class="pp-block-empty">还没有步骤</p>
        <ol v-else class="pp-steps">
          <li v-for="(nodeId, index) in selectedPath.node_ids" :key="`${nodeId}-${index}`" class="pp-step">
            <span class="pp-step-index">{{ index + 1 }}</span>
            <img
              v-if="nodeById(nodeId)?.screenshot"
              class="pp-step-thumb"
              :src="shotUrl(nodeById(nodeId).screenshot)"
              :alt="nodeById(nodeId)?.title || nodeId"
            />
            <span v-else class="pp-step-thumb is-empty"></span>
            <span class="pp-step-title">{{ nodeById(nodeId)?.title || nodeId }}</span>
            <span v-if="annotationsFor(selectedPath, nodeId).length" class="pp-annotated">已标注</span>
            <span v-if="selectedPath.annotation_invalidated_nodes?.includes(nodeId)" class="pp-annotation-stale">需重标</span>
            <button class="pp-annotate" type="button" title="添加路径专属标注" @click="addAnnotation(selectedPath, nodeId)">标注</button>
            <button v-if="annotationsFor(selectedPath, nodeId).length" class="pp-annotate-clear" type="button" title="清除标注" @click="clearAnnotations(selectedPath, nodeId)">清除</button>
            <button class="pp-step-del" type="button" title="从路径移除" @click="removeStep(selectedPath, nodeId)">
              ×
            </button>
          </li>
        </ol>

        <!-- 添加步骤 -->
        <details class="pp-add">
          <summary>+ 添加步骤</summary>
          <ul v-if="addableNodes.length" class="pp-add-list">
            <li v-for="n in addableNodes" :key="n.id">
              <button type="button" @click="addStep(selectedPath, n.id)">{{ n.title || n.id }}</button>
            </li>
          </ul>
          <p v-else class="pp-block-empty">画布上的节点都已加入本路径</p>
        </details>
      </section>

      <!-- 推库字段 -->
      <section class="pp-block">
        <h4 class="pp-block-title">推送信息</h4>

        <div class="pp-field">
          <label for="pp-question">推库问题</label>
          <input
            id="pp-question"
            v-model="fieldForm.question"
            type="text"
            placeholder="用户会怎么问这个问题"
            @focus="onFocus('question')"
            @blur="onBlur('question')"
          />
        </div>

        <div class="pp-field-row">
          <div class="pp-field">
            <label for="pp-category">产品品类</label>
            <select
              id="pp-category"
              v-model="fieldForm.product_category_name"
              @focus="onFocus('product_category_name')"
              @change="onCategoryChange"
            >
              <option value="">（不填）</option>
              <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
            </select>
          </div>

          <div class="pp-field">
            <label for="pp-model">产品型号</label>
            <select
              id="pp-model"
              v-model="fieldForm.product_name"
              @focus="onFocus('product_name')"
              @blur="onBlur('product_name')"
            >
              <option value="">（不填）</option>
              <option
                v-for="m in (fieldForm.product_name && !modelsOfCategory.includes(fieldForm.product_name)
                  ? [fieldForm.product_name, ...modelsOfCategory]
                  : modelsOfCategory)"
                :key="m"
                :value="m"
              >
                {{ m }}
              </option>
            </select>
          </div>
        </div>

        <div class="pp-field">
          <label for="pp-wiki">知识库ID</label>
          <input
            id="pp-wiki"
            v-model="fieldForm.kb_wiki_id"
            type="text"
            :placeholder="selectedPath.id"
            @focus="onFocus('kb_wiki_id')"
            @blur="onBlur('kb_wiki_id')"
          />
        </div>
      </section>

      <!-- 操作按钮 -->
      <section class="pp-block">
        <div class="pp-buttons">
          <button class="btn" type="button" :disabled="rendering" @click="doRender(selectedPath)">
            {{ rendering ? '渲染中…' : '重渲染长图与文档' }}
          </button>
          <button class="btn" type="button" :disabled="publishing" @click="doPublish(selectedPath)">
            {{ publishing ? '推送中…' : '推送知识库' }}
          </button>
          <button
            class="btn"
            type="button"
            :disabled="loadingResult || !selectedPath.long_image_url"
            @click="toggleResult(selectedPath)"
          >
            {{ loadingResult ? '读取中…' : '查看生成结果' }}
          </button>
        </div>

        <!-- 推送结果 -->
        <div v-if="publishResult" class="pp-publish" :class="publishResult.ok ? 'is-ok' : 'is-error'">
          <p class="pp-publish-title">{{ publishResult.ok ? '推送成功' : '推送失败' }}</p>
          <!-- Hub 返回的中文报错原文原样展示，不做翻译或包装 -->
          <p v-if="publishResult.text" class="pp-publish-text">{{ publishResult.text }}</p>
          <p v-if="publishResult.entryId" class="pp-publish-entry">条目 ID：{{ publishResult.entryId }}</p>
        </div>
      </section>

      <!-- 生成结果预览 -->
      <section v-if="showResult && renderResult" class="pp-block">
        <h4 class="pp-block-title">生成结果</h4>
        <div v-if="renderResult.longImageUrl" class="pp-image-actions">
          <button class="btn" type="button" @click="openImagePreview(renderResult.longImageUrl)">放大预览</button>
          <button class="btn" type="button" @click="copyImage(renderResult.longImageUrl)">复制图片</button>
        </div>
        <img
          v-if="renderResult.longImageUrl"
          class="pp-longimg"
          :src="renderResult.longImageUrl"
          alt="长图预览"
          title="点击放大预览"
          @click="openImagePreview(renderResult.longImageUrl)"
        />
        <pre v-if="renderResult.markdown" class="pp-markdown">{{ renderResult.markdown }}</pre>
        <p v-if="!renderResult.longImageUrl && !renderResult.markdown" class="pp-block-empty">没有可预览的内容</p>
      </section>

      <div
        v-if="previewImage"
        class="pp-image-lightbox"
        role="dialog"
        aria-modal="true"
        aria-label="长图放大预览"
        @click.self="closeImagePreview"
        @keydown.esc="closeImagePreview"
        tabindex="0"
      >
        <button class="pp-lightbox-close" type="button" aria-label="关闭预览" @click="closeImagePreview">×</button>
        <img :src="previewImage" alt="长图放大预览" />
      </div>
    </div>
  </section>
  <AnnotationEditor v-if="annotationTarget" :key="`${annotationTarget.pathId}:${annotationTarget.nodeId}`" :image-url="shotUrl(annotationTarget.screenshot)" :title="annotationTarget.title" :annotations="annotationTarget.marks" :saving="annotationSaving" @save="saveAnnotations" @close="!annotationSaving && (annotationTarget = null)" />
</template>

<style scoped>
.path-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  text-align: left;
  color: #1f2430;
  font-size: 14px;
  line-height: 1.5;
}

.pp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.pp-head h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #1f2430;
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
  opacity: 0.55;
  cursor: not-allowed;
}

.btn-primary {
  background: #2b6cff;
  border-color: #2b6cff;
  color: #fff;
}

.btn-primary:hover:not(:disabled) {
  background: #1f5ae0;
  border-color: #1f5ae0;
  color: #fff;
}

.pp-create {
  display: flex;
  flex-direction: column;
  gap: 6px;
  border: 1px solid #e5e8ec;
  border-radius: 10px;
  padding: 10px;
  background: #fafbfc;
}

.pp-create label {
  font-size: 12px;
  font-weight: 600;
  color: #4a5160;
}

.pp-create input[type='text'],
.pp-create select {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #dfe3e8;
  border-radius: 8px;
  padding: 7px 9px;
  font-size: 13px;
  font-family: inherit;
  color: #1f2430;
  background: #fff;
}

.pp-create-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 2px;
}

.pp-empty {
  margin: 0;
  padding: 16px 10px;
  text-align: center;
  color: #9aa1ab;
  font-size: 13px;
  background: #fafbfc;
  border-radius: 10px;
}

.pp-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 210px;
  overflow-y: auto;
}

.pp-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid #e5e8ec;
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.pp-item:hover {
  border-color: #c9d6f5;
  background: #fbfcff;
}

.pp-item.is-active {
  border-color: #2b6cff;
  background: #f2f6ff;
}

.pp-item-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.pp-item-title {
  font-size: 13px;
  color: #1f2430;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pp-item-steps {
  font-size: 11px;
  color: #9aa1ab;
}

.pp-badge {
  flex: none;
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 20px;
  border: 1px solid transparent;
}

.pp-badge.is-warn {
  color: #b06a00;
  background: #fff5e6;
  border-color: #ffd8a8;
}

.pp-badge.is-ok {
  color: #12a150;
  background: #eafaf1;
  border-color: #b6e8ce;
}

.pp-badge.is-muted {
  color: #8a9099;
  background: #f2f3f5;
  border-color: #e2e5e9;
}

.pp-del {
  flex: none;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: #b6bcc4;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.pp-del:hover {
  background: #e5484d;
  color: #fff;
}

.pp-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
  border-top: 1px solid #eceef1;
  padding-top: 12px;
}

.pp-title-input {
  font-weight: 600;
}

.pp-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pp-derive-toggle {
  align-self: flex-start;
}

.pp-derive {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 9px;
  border: 1px solid #e5e8ec;
  border-radius: 8px;
  background: #fafbfc;
}

.pp-derive > label {
  color: #4a5160;
  font-size: 12px;
  font-weight: 600;
}

.pp-derive select {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #dfe3e8;
  border-radius: 8px;
  padding: 7px 9px;
  color: #1f2430;
  background: #fff;
  font: inherit;
}

.pp-derived-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  max-height: 150px;
  overflow-y: auto;
  margin: 2px 0;
  padding: 0;
  list-style: none;
}

.pp-derived-list label {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  color: #4a5160;
  font-size: 12px;
  overflow-wrap: anywhere;
}

.pp-block-title {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: #4a5160;
}

.pp-block-empty {
  margin: 0;
  font-size: 12px;
  color: #9aa1ab;
}

.pp-steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.pp-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid #eceef1;
  border-radius: 8px;
  background: #fff;
}

.pp-step-index {
  flex: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #2b6cff;
  color: #fff;
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.pp-step-thumb {
  flex: none;
  width: 30px;
  height: 40px;
  object-fit: cover;
  border-radius: 5px;
  border: 1px solid #e5e8ec;
  background: #f7f8fa;
}

.pp-step-thumb.is-empty {
  border-style: dashed;
  border-color: #c9d0da;
}

.pp-step-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pp-annotate,
.pp-annotate-clear {
  border: 1px solid #d8e2f3;
  border-radius: 5px;
  background: #f5f8ff;
  color: #2563eb;
  padding: 3px 6px;
  font-size: 11px;
  cursor: pointer;
}

.pp-annotate-clear { color: #b42318; background: #fff7f6; border-color: #f2c7c2; }
.pp-annotated { color: #16794c; font-size: 11px; }
.pp-annotation-stale { color: #b54708; font-size: 11px; font-weight: 600; }

.pp-step-del {
  flex: none;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: #b6bcc4;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.pp-step-del:hover {
  background: #e5484d;
  color: #fff;
}

.pp-add summary {
  font-size: 12px;
  color: #2b6cff;
  cursor: pointer;
  user-select: none;
}

.pp-add-list {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.pp-add-list button {
  border: 1px solid #dfe3e8;
  background: #fff;
  border-radius: 20px;
  padding: 4px 10px;
  font-size: 12px;
  color: #4a5160;
  cursor: pointer;
}

.pp-add-list button:hover {
  border-color: #2b6cff;
  color: #2b6cff;
}

.pp-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-width: 0;
}

.pp-field-row {
  display: flex;
  gap: 8px;
}

.pp-field label {
  font-size: 12px;
  font-weight: 600;
  color: #4a5160;
}

.pp-field input[type='text'],
.pp-field select {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #dfe3e8;
  border-radius: 8px;
  padding: 7px 9px;
  font-size: 13px;
  font-family: inherit;
  color: #1f2430;
  background: #fff;
}

.pp-field input[type='text']:focus,
.pp-field select:focus {
  outline: none;
  border-color: #2b6cff;
  box-shadow: 0 0 0 3px rgba(43, 108, 255, 0.12);
}

.pp-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.pp-publish {
  border-radius: 8px;
  padding: 9px 11px;
  border: 1px solid transparent;
}

.pp-publish.is-ok {
  background: #eafaf1;
  border-color: #b6e8ce;
}

.pp-publish.is-error {
  background: #fdf2f2;
  border-color: #f3c6c6;
}

.pp-publish-title {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
}

.pp-publish.is-ok .pp-publish-title {
  color: #12a150;
}

.pp-publish.is-error .pp-publish-title {
  color: #e5484d;
}

.pp-publish-text {
  margin: 4px 0 0;
  font-size: 12px;
  color: #4a5160;
  white-space: pre-wrap;
  word-break: break-word;
}

.pp-publish-entry {
  margin: 4px 0 0;
  font-size: 12px;
  color: #8a9099;
}

.pp-image-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.pp-longimg {
  width: 100%;
  cursor: zoom-in;
}

.pp-image-lightbox {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 28px;
  background: rgba(15, 20, 30, 0.78);
}

.pp-lightbox-close {
  position: absolute;
  top: 14px;
  right: 18px;
  width: 40px;
  height: 40px;
  border: 0;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
  font-size: 28px;
  line-height: 1;
  cursor: pointer;
}

.pp-image-lightbox img {
  max-width: 96vw;
  max-height: 94vh;
  object-fit: contain;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.35);
}

.pp-longimg {
  width: 100%;
  border-radius: 8px;
  border: 1px solid #e5e8ec;
  background: #f7f8fa;
}

.pp-markdown {
  margin: 0;
  max-height: 260px;
  overflow: auto;
  background: #f7f8fa;
  border: 1px solid #e5e8ec;
  border-radius: 8px;
  padding: 10px;
  font-family: ui-monospace, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #1f2430;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
