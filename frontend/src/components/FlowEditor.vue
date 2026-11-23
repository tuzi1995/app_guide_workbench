<script setup>
/**
 * 画布：把「APP 截图 + 操作说明」渲染成节点，用连线表达操作路径。
 *
 * 关键点（已对照 @logicflow/core 2.2.5 源码核实）：
 * 1. LogicFlow v2 的 HtmlNode / HtmlNodeModel 是「具名导出」，不在默认导出对象上。
 *    写成 `class X extends LogicFlow.RectNode` 会得到 undefined，
 *    setup 抛 "Class extends value undefined" 直接让整个页面白屏。
 *    正确写法：`import LogicFlow, { HtmlNode, HtmlNodeModel } from '@logicflow/core'`
 * 2. 自定义节点必须用 HtmlNode（view）+ HtmlNodeModel（model）成对注册，
 *    内部用 <foreignObject> 挂真实 HTML，才能显示 <img>。
 * 3. 边的字段名是 sourceNodeId / targetNodeId（lib/util/edge.js:860 pickEdgeConfig），
 *    写成 source / target 会被直接丢掉，连线画不出来。
 * 4. `blank:drop` 是 LogicFlow 自己按住拖动画布时发的 pointer 事件，
 *    和素材池用的「原生 HTML5 拖放」不是一回事，所以要自己挂 dragover/drop 监听。
 * 5. lf.getPointByClient(x, y) 返回 { domOverlayPosition, canvasOverlayPosition }。
 */
import { onMounted, onBeforeUnmount, watch, nextTick, ref, computed } from 'vue'
import LogicFlow from '@logicflow/core'
import '@logicflow/core/dist/index.css'
import { AutoLayout } from '@logicflow/extension'
import { state, actions } from '../store'
import {
  NODE_TYPE,
  NODE_WIDTH,
  NODE_HEIGHT,
  buildNodeProperties,
  registerAppNode,
  injectNodeStyles,
  mediaUrl,
} from '../flowNode'

let lf = null
let syncing = false // 防止 store → 画布的回写又触发画布 → store，形成循环
let fitTimer = null // 画布尺寸变化后的「适应画布」防抖
const zoomPercent = ref(100)
const searchText = ref('')
const searchFocused = ref(false)
const expanded = ref(false)

const matchingNodes = computed(() => {
  const query = searchText.value.trim().toLocaleLowerCase()
  if (!query) return []
  return (state.graph?.nodes || []).filter((node) =>
    [node.title, node.action, node.screenshot]
      .some((value) => String(value || '').toLocaleLowerCase().includes(query)),
  ).slice(0, 8)
})

function updateZoomLabel() {
  if (!lf) return
  const { SCALE_X, SCALE_Y } = lf.getTransform()
  zoomPercent.value = Math.round(((SCALE_X + SCALE_Y) / 2) * 100)
}

function onWindowKeydown(event) {
  if (event.key === 'Escape' && expanded.value) expanded.value = false
}

/**
 * 画布容器尺寸变了之后要重新适应一次，否则节点会被裁到可视区外。
 * LogicFlow 自带 ResizeObserver 只更新了画布宽高，不会重算视图缩放/平移。
 */
function scheduleFitView() {
  if (!lf) return
  if (fitTimer) clearTimeout(fitTimer)
  fitTimer = setTimeout(() => {
    fitTimer = null
    try {
      lf?.fitView(40, 40)
    } catch (err) {
      /* 画布还没准备好时忽略 */
    }
  }, 80)
}

/* ------------------------------------------------------------------ */
/* 数据 ↔ 画布 的双向转换                                             */
/* ------------------------------------------------------------------ */

function nodeToLf(node, index) {
  return {
    id: node.id,
    type: NODE_TYPE,
    x: Number(node.x) || 120,
    y: Number(node.y) || 120,
    properties: buildNodeProperties(node, String(index + 1)),
  }
}

/**
 * 后端的边用 source / target，LogicFlow 用 sourceNodeId / targetNodeId。
 * 渲染时转换过去，落库时再换回来。
 */
function edgeToLf(edge) {
  return {
    id: edge.id,
    type: 'line',
    sourceNodeId: edge.source,
    targetNodeId: edge.target,
    properties: { label: edge.label || '' },
  }
}

function indexOfNode(id) {
  const index = (state.graph?.nodes || []).findIndex((n) => n.id === id)
  return index >= 0 ? String(index + 1) : '·'
}

function renderGraph() {
  if (!lf) return
  syncing = true
  const nodes = (state.graph?.nodes || []).map(nodeToLf)
  const edges = (state.graph?.edges || []).map(edgeToLf)
  lf.render({ nodes, edges })
  syncing = false
}

/** 只更新节点的展示属性，不动位置与连线 */
function refreshNodeProps(node) {
  if (!lf) return
  const model = lf.getNodeModelById(node.id)
  if (!model) return
  const props = buildNodeProperties(node, indexOfNode(node.id))
  delete props.width
  delete props.height
  lf.setProperties(node.id, props)
}

/* ------------------------------------------------------------------ */
/* 事件绑定                                                            */
/* ------------------------------------------------------------------ */

function bindEvents(container) {
  // 画布容器大小变化 → 重新适应，避免节点被裁到可视区外
  lf.on('graph:resize', () => {
    scheduleFitView()
  })
  lf.on('graph:transform', updateZoomLabel)

  // 选中节点 → 同步到右侧检查器
  lf.on('node:click', ({ data }) => {
    // 点击节点前清理残留的多选状态，保证当前节点可正常选中。
    lf?.graphModel?.clearSelectElements?.()
    actions.selectNode(data?.id || '')
  })

  // 空白处点击 → 取消选中
  lf.on('blank:click', () => {
    actions.selectNode('')
    actions.selectEdge('')
  })

  // 拖动结束 → 保存新坐标（LogicFlow 没有 node:dragend，只有 node:drop）
  lf.on('node:drop', ({ data }) => {
    if (syncing) return
    const model = lf.getNodeModelById(data?.id)
    if (!model) return
    actions.updateNode(data.id, { x: Math.round(model.x), y: Math.round(model.y) })
  })

  // 画布上新建节点 → 落库
  lf.on('node:add', ({ data }) => {
    if (syncing) return
    if (data?.id && (state.graph?.nodes || []).some((n) => n.id === data.id)) return
    actions.createNode({
      id: data?.id,
      title: '新步骤',
      action: '',
      screenshot: '',
      x: Math.round(data?.x || 120),
      y: Math.round(data?.y || 120),
    })
  })

  // 连线建立 → 落库（注意是 sourceNodeId / targetNodeId）
  lf.on('edge:add', ({ data }) => {
    if (syncing) return
    if (data?.id && (state.graph?.edges || []).some((e) => e.id === data.id)) return
    actions.createEdge({
      id: data?.id,
      source: data?.sourceNodeId,
      target: data?.targetNodeId,
      label: data?.text?.value || '',
    })
  })

  // 删除连线 → 落库
  lf.on('edge:delete', ({ data }) => {
    if (syncing) return
    actions.removeEdge(data?.id)
  })

  // 节点上的按钮：替换截图 / 删除节点（事件委托，绑在画布容器上）
  const handleNodeAction = (event) => {
    const detail = event.detail || {}
    const nodeId = detail.nodeId
    if (!nodeId) return
    event.stopPropagation()
    if (detail.action === 'delete') {
      if (!window.confirm('确认删除这个节点？相关连线和路径步骤会一并移除。')) return
      actions.removeNode(nodeId)
      actions.toast('success', '节点已删除')
    } else if (detail.action === 'replace') {
      // 选中该节点，右侧属性面板的「替换截图」即可使用
      actions.selectNode(nodeId)
      actions.toast('info', '已选中该节点，请在右侧「节点属性」里替换截图')
    }
  }
  container.addEventListener('ag-node-action', handleNodeAction)
}

/**
 * 素材池 → 画布：用的是原生 HTML5 拖放，
 * LogicFlow 的 blank:drop 是它自己拖动画布时的 pointer 事件，接收不到 dataTransfer。
 */
function bindNativeDrop(container) {
  container.addEventListener('dragover', (event) => {
    if (!event.dataTransfer) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
  })

  container.addEventListener('drop', (event) => {
    event.preventDefault()
    const raw = event.dataTransfer?.getData('application/x-app-shot')
    if (!raw) return
    let payload
    try {
      payload = JSON.parse(raw)
    } catch (err) {
      return
    }
    // getPointByClient 返回 { domOverlayPosition, canvasOverlayPosition }
    const point = lf.getPointByClient(event.clientX, event.clientY)
    const x = point?.canvasOverlayPosition?.x ?? 200
    const y = point?.canvasOverlayPosition?.y ?? 200
    actions.createNode({
      title: payload.title || '新步骤',
      action: payload.action || '',
      screenshot: payload.filename || '',
      x: Math.round(x),
      y: Math.round(y),
    })
    actions.toast('success', '已新建节点')
  })
}

/* ------------------------------------------------------------------ */
/* 生命周期                                                            */
/* ------------------------------------------------------------------ */

onMounted(async () => {
  const container = document.querySelector('#lf-container')
  if (!container) return

  injectNodeStyles()

  lf = new LogicFlow({
    container,
    grid: true,
    gridType: 'dot',
    edgeType: 'line',
    allowCreateEdge: true,
    allowDeleteNode: true,
    allowDeleteEdge: true,
    allowDraggingNode: true,
    // 保留节点基础点击选中，只隐藏悬停轮廓以免蓝框盖住节点。
    hoverOutline: false,
    anchor: {
      type: 'input',
      // 四周四个锚点，方便拖出连线
      anchors: [
        { id: 'top', type: 'top', visible: true },
        { id: 'right', type: 'right', visible: true },
        { id: 'bottom', type: 'bottom', visible: true },
        { id: 'left', type: 'left', visible: true },
      ],
    },
    edgeText: { editable: false },
    allowEdgeLabel: true,
    nodeText: { editable: false },
  })

  registerAppNode(lf)
  bindEvents(container)
  bindNativeDrop(container)
  window.addEventListener('keydown', onWindowKeydown)

  await nextTick()
  renderGraph()

  lf.setDefaultEdgeType('line')

  // 首次渲染后把节点缩放到可视区内
  await nextTick()
  try {
    lf.fitView(40, 40)
    updateZoomLabel()
  } catch (err) {
    /* 画布还没量到尺寸时忽略 */
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onWindowKeydown)
  if (fitTimer) {
    clearTimeout(fitTimer)
    fitTimer = null
  }
  if (lf) {
    lf.destroy()
    lf = null
  }
})

// store 里的节点变化 → 同步到画布
watch(
  () =>
    (state.graph?.nodes || [])
      .map((n) => `${n.id}:${n.screenshot}:${n.title}:${n.action}:${n.changed}`)
      .join('|'),
  () => {
    if (!lf) return
    const ids = new Set((state.graph?.nodes || []).map((n) => n.id))
    // 画布上有、store 里没有的节点 → 从画布删掉
    for (const el of lf.getGraphData().nodes) {
      if (!ids.has(el.id)) lf.deleteNode(el.id)
    }
    for (const node of state.graph.nodes) {
      const exists = lf.getNodeModelById(node.id)
      if (!exists) {
        const index = (state.graph?.nodes || []).findIndex((n) => n.id === node.id)
        lf.addNode(nodeToLf(node, index))
      } else {
        refreshNodeProps(node)
      }
    }
  },
  { deep: false },
)

// store 里的连线变化 → 同步到画布
watch(
  () => (state.graph?.edges || []).map((e) => `${e.id}:${e.source}>${e.target}`).join('|'),
  () => {
    if (!lf) return
    const want = new Set((state.graph?.edges || []).map((e) => e.id))
    for (const el of lf.getGraphData().edges) {
      if (!want.has(el.id)) lf.deleteEdge(el.id)
    }
    for (const edge of state.graph.edges) {
      const exists = lf.getEdgeModelById(edge.id)
      if (!exists) {
        lf.addEdge(edgeToLf(edge))
      }
    }
  },
  { deep: false },
)

// 选中状态 → 画布高亮
watch(
  () => state.selectedNodeId,
  (id) => {
    if (!lf) return
    // `selectElementById(id, multiple)` 的第二参数为“是否追加选择”，
    // 不能在循环中对每个节点传 false，否则前一个选择会被后一个节点覆盖。
    lf.graphModel?.clearSelectElements?.()
    if (id) lf.selectElementById(id, false)
  },
)

/* ------------------------------------------------------------------ */
/* 工具栏动作                                                          */
/* ------------------------------------------------------------------ */

function onSave() {
  actions.saveGraph()
}

function onAutoLayout() {
  if (!lf) return
  try {
    // AutoLayout 的构造函数签名是 { lf }，它会把 lf.layout() 挂到实例上
    new AutoLayout({ lf })
    lf.layout()
    // 布局后把新坐标写回 store
    for (const el of lf.getGraphData().nodes) {
      const model = lf.getNodeModelById(el.id)
      if (!model) continue
      actions.updateNode(el.id, { x: Math.round(model.x), y: Math.round(model.y) })
    }
    actions.toast('success', '已自动排列节点')
  } catch (err) {
    actions.toast('error', `自动布局失败：${err?.message || err}`)
  }
}

function onFitView() {
  lf?.fitView()
  updateZoomLabel()
}

function onZoomIn() {
  lf?.zoom(1.15)
}

function onZoomOut() {
  lf?.zoom(0.87)
}

function focusNode(node) {
  if (!lf || !node) return
  actions.selectNode(node.id)
  lf.focusOn(node.id)
  searchText.value = node.title || ''
  searchFocused.value = false
}

function onHelp() {
  actions.toast(
    'info',
    '从左侧素材池把截图拖到画布即可建节点；在节点边缘的锚点上按住拖出连线；选中节点后可在右侧改标题与操作说明。',
  )
}

defineExpose({ lf })
</script>

<template>
  <div class="flow-editor" :class="{ 'is-expanded': expanded }">
    <div class="fe-toolbar">
      <button class="btn btn-primary" type="button" :disabled="state.saving" @click="onSave">
        {{ state.saving ? '保存中…' : '保存画布' }}
      </button>
      <button class="btn" type="button" @click="onAutoLayout">自动布局</button>
      <button class="btn" type="button" @click="onFitView">适应画布</button>
      <div class="fe-zoom" aria-label="画布缩放">
        <button class="btn fe-zoom-btn" type="button" aria-label="缩小画布" title="缩小画布" @click="onZoomOut">−</button>
        <span class="fe-zoom-value">{{ zoomPercent }}%</span>
        <button class="btn fe-zoom-btn" type="button" aria-label="放大画布" title="放大画布" @click="onZoomIn">+</button>
      </div>
      <div class="fe-search-wrap">
        <input
          v-model="searchText"
          class="fe-search"
          type="search"
          placeholder="搜索节点名称或说明"
          aria-label="搜索节点名称或说明"
          @focus="searchFocused = true"
          @keydown.enter.prevent="matchingNodes[0] && focusNode(matchingNodes[0])"
          @keydown.esc.stop="searchFocused = false"
        />
        <div v-if="searchFocused && searchText.trim()" class="fe-search-results">
          <button
            v-for="node in matchingNodes"
            :key="node.id"
            class="fe-search-result"
            type="button"
            @mousedown.prevent="focusNode(node)"
          >
            <strong>{{ node.title || '未命名节点' }}</strong>
            <span>{{ node.action || node.screenshot || '暂无说明' }}</span>
          </button>
          <p v-if="!matchingNodes.length" class="fe-search-empty">没有匹配的节点</p>
        </div>
      </div>
      <button class="btn" type="button" @click="expanded = !expanded">
        {{ expanded ? '收起画布' : '展开画布' }}
      </button>
      <button class="btn" type="button" @click="onHelp">使用说明</button>
      <span class="fe-tip">拖入截图建节点 · 锚点拖出连线 · 单击节点编辑</span>
    </div>
    <div id="lf-container" class="fe-canvas"></div>
  </div>
</template>

<style scoped>
.flow-editor {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: #f7f8fa;
}

.flow-editor.is-expanded {
  position: fixed;
  inset: 0;
  z-index: 1000;
  width: 100vw;
  height: 100dvh;
}

.fe-toolbar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #fff;
  border-bottom: 1px solid #eceef1;
  flex-wrap: wrap;
  white-space: nowrap;
}

.fe-zoom {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 4px;
  border: 1px solid #e5e8ec;
  border-radius: 8px;
  background: #f8f9fb;
}

.fe-zoom .fe-zoom-btn {
  width: 30px;
  height: 30px;
  padding: 0;
  font-size: 18px;
  line-height: 1;
}

.fe-zoom-value {
  min-width: 48px;
  color: #596273;
  font-size: 12px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.fe-search-wrap {
  position: relative;
  flex: 0 1 220px;
  min-width: 150px;
}

.fe-search {
  box-sizing: border-box;
  width: 100%;
  height: 34px;
  padding: 0 10px;
  border: 1px solid #dfe3e8;
  border-radius: 8px;
  color: #1f2430;
  font-size: 13px;
}

.fe-search:focus {
  border-color: #2b6cff;
  outline: 2px solid #2b6cff22;
}

.fe-search-results {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 20;
  width: min(340px, 80vw);
  max-height: 320px;
  overflow-y: auto;
  border: 1px solid #dfe3e8;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 8px 24px #202b3c24;
}

.fe-search-result {
  display: flex;
  width: 100%;
  flex-direction: column;
  gap: 3px;
  padding: 9px 11px;
  border: 0;
  border-bottom: 1px solid #eef0f3;
  background: #fff;
  color: #273142;
  text-align: left;
  cursor: pointer;
}

.fe-search-result:hover { background: #f3f7ff; }
.fe-search-result span,
.fe-search-empty {
  overflow: hidden;
  color: #818b99;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fe-search-empty { margin: 0; padding: 12px; }

.fe-canvas {
  flex: 1;
  min-height: 0;
  width: 100%;
}

.fe-tip {
  margin-left: auto;
  font-size: 12px;
  color: #98a0ad;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

@media (max-width: 900px) {
  .fe-tip { display: none; }
  .fe-toolbar { gap: 6px; padding: 7px; }
  .fe-search-wrap { flex: 1 1 180px; }
}

.btn {
  border: 1px solid #dfe3e8;
  background: #fff;
  color: #1f2430;
  border-radius: 8px;
  padding: 6px 12px;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
  flex: none;
}

.btn:hover {
  border-color: #2b6cff;
  color: #2b6cff;
}

.btn:disabled {
  opacity: 0.6;
  cursor: default;
}

.btn-primary {
  background: #2b6cff;
  border-color: #2b6cff;
  color: #fff;
}

.btn-primary:hover {
  color: #fff;
  opacity: 0.9;
}
</style>
