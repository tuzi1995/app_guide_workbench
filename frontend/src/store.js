// 全局状态：画布图文档、素材库、路径列表、提示消息。
// 组件之间不直接互相调用，统一通过这里的 state / actions 通信。
import { reactive } from 'vue'
import api from './api'

export const state = reactive({
  // 事实源：整张画布（节点 + 连线 + 路径）
  graph: { title: '', nodes: [], edges: [], paths: [] },
  // 截图素材池
  media: [],
  // 路径列表（从后端 /api/paths 拉，带渲染状态）
  paths: [],
  // 选中的东西
  selectedNodeId: '',
  selectedEdgeId: '',
  selectedPathId: '',
  // 右键/消息提示
  toasts: [],
  // 知识库型号库：[{ category, models: [...] }]，给下拉框用
  models: [],
  // 知识库连接状态
  hub: { loggedIn: false, username: '', message: '' },
  // 最近一次渲染结果（供预览面板展示长图 + Markdown）
  lastRender: null,
  // lastRender 的别名，方便不同面板按习惯取用
  renderResult: null,
  render: null,
  loading: false,
  saving: false,
  backendOnline: false,
})

let toastSeq = 0

export const actions = {
  // ---------------------------------------------------------- 提示消息
  toast(type, message) {
    const id = ++toastSeq
    state.toasts.push({ id, type, message, text: message })
    setTimeout(() => {
      const idx = state.toasts.findIndex((t) => t.id === id)
      if (idx >= 0) state.toasts.splice(idx, 1)
    }, 3600)
    return id
  },

  dismissToast(id) {
    const idx = state.toasts.findIndex((t) => t.id === id)
    if (idx >= 0) state.toasts.splice(idx, 1)
  },

  // ---------------------------------------------------------- 初始化
  async init() {
    state.loading = true
    try {
      await actions.loadGraph()
      await Promise.all([actions.loadMedia(), actions.loadPaths(), actions.loadModels()])
      actions.checkHub()
      state.backendOnline = true
    } catch (err) {
      state.backendOnline = false
      actions.toast('error', err?.message || '初始化失败')
    } finally {
      state.loading = false
    }
  },

  async loadGraph() {
    const res = await api.getGraph()
    const g = res?.data || res
    state.graph = {
      title: g?.title || '',
      nodes: g?.nodes || [],
      edges: g?.edges || [],
      paths: g?.paths || [],
    }
    return state.graph
  },

  async saveGraph() {
    state.saving = true
    try {
      const res = await api.saveGraph()
      const data = res?.data || {}
      if (data.warnings && Object.keys(data.warnings).length) {
        actions.toast('warn', data.warning_text || '画布已保存，但部分路径的连线不完整')
      } else {
        actions.toast('success', '画布已保存')
      }
      await actions.loadPaths()
      return res
    } catch (err) {
      actions.toast('error', `保存失败：${err?.message || err}`)
      throw err
    } finally {
      state.saving = false
    }
  },

  // ---------------------------------------------------------- 素材库
  async loadMedia() {
    const res = await api.listMedia()
    const list = res?.data || []
    state.media = Array.isArray(list) ? list : []
    return state.media
  },

  async uploadMedia(file) {
    const res = await api.uploadMedia(file)
    const item = res?.data || res
    // 直接插到列表最前面，避免用户等待整表刷新
    if (item?.filename && !state.media.some((m) => m.filename === item.filename)) {
      state.media.unshift(item)
    }
    return res
  },

  async deleteMedia(filename) {
    const res = await api.deleteMedia(filename)
    state.media = state.media.filter((m) => m.filename !== filename)
    return res
  },

  // ---------------------------------------------------------- 节点
  async createNode(partial = {}) {
    const res = await api.createNode(partial)
    const node = res?.data || res
    if (node?.id) state.graph.nodes.push(node)
    return node
  },

  /** 更新节点属性：先本地改，再同步后端，失败则回滚。 */
  async updateNode(nodeId, patch = {}) {
    const node = state.graph.nodes.find((n) => n.id === nodeId)
    if (!node) return null
    const backup = {}
    for (const key of Object.keys(patch)) backup[key] = node[key]
    Object.assign(node, patch)
    try {
      const res = await api.updateNode(nodeId, patch)
      const fresh = res?.data || res
      if (fresh && typeof fresh === 'object') Object.assign(node, fresh)
      // 后端在改了标题/操作/截图后会沿连线自动重渲染受影响的路径，
      // 这里把路径列表刷新一下，让右侧面板的「需重渲染」徽标及时更新
      if (fresh?.affected_paths?.length) {
        await actions.loadPaths()
      }
      return node
    } catch (err) {
      Object.assign(node, backup)
      actions.toast('error', `更新节点失败：${err?.message || err}`)
      throw err
    }
  },

  async removeNode(nodeId) {
    const res = await api.deleteNode(nodeId)
    state.graph.nodes = state.graph.nodes.filter((n) => n.id !== nodeId)
    state.graph.edges = state.graph.edges.filter(
      (e) => e.source !== nodeId && e.target !== nodeId,
    )
    state.graph.paths.forEach((p) => {
      if ((p.node_ids || []).includes(nodeId)) {
        p.node_ids = p.node_ids.filter((id) => id !== nodeId)
        p.stale = true
      }
    })
    if (state.selectedNodeId === nodeId) state.selectedNodeId = ''
    await actions.loadPaths()
    return res
  },

  /**
   * 替换某个节点的截图 —— 需求里「局部改版」的入口。
   * 后端会沿连线找出受影响的路径并自动重新生成拼接长图与文字步骤文档，
   * 这里把结果同步回本地状态。
   */
  async uploadScreenshot(nodeId, file) {
    const res = await api.uploadNodeScreenshot(nodeId, file)
    const data = res?.data || {}
    const node = state.graph.nodes.find((n) => n.id === nodeId)
    if (node && data.node) Object.assign(node, data.node)
    if (data.affected_paths?.length) {
      await actions.loadPaths()
    }
    return res
  },

  // ---------------------------------------------------------- 连线
  async createEdge(edge) {
    const res = await api.createEdge(edge)
    const created = res?.data || res
    if (created?.id) state.graph.edges.push(created)
    return created
  },

  async removeEdge(edgeId) {
    const res = await api.deleteEdge(edgeId)
    state.graph.edges = state.graph.edges.filter((e) => e.id !== edgeId)
    if (state.selectedEdgeId === edgeId) state.selectedEdgeId = ''
    return res
  },

  // ---------------------------------------------------------- 路径
  async loadPaths() {
    const res = await api.listPaths()
    const list = res?.data || []
    state.paths = Array.isArray(list) ? list : []
    // 以服务端列表为准同步新增、删除和状态；保留已有对象，供面板继续引用。
    // 保存画布也读取 graph.paths，不能只刷新已有条目的字段。
    const existing = new Map(state.graph.paths.map((p) => [p.id, p]))
    state.graph.paths = state.paths.map((p) => {
      const local = existing.get(p.id)
      return local ? Object.assign(local, p) : p
    })
    if (state.selectedPathId && !state.graph.paths.some((p) => p.id === state.selectedPathId)) {
      state.selectedPathId = ''
    }
    return state.paths
  },

  async createPath(path) {
    const res = await api.createPath(path)
    const created = res?.data || res
    await actions.loadPaths()
    actions.selectPath(created.id)
    return created
  },

  /** addPath = createPath 的别名，两个叫法都留着。 */
  addPath(path) {
    return actions.createPath(path)
  },

  async updatePath(pathId, patch) {
    const res = await api.updatePath(pathId, patch)
    await actions.loadPaths()
    return res
  },

  async removePath(pathId) {
    const res = await api.deletePath(pathId)
    await actions.loadPaths()
    if (state.selectedPathId === pathId) state.selectedPathId = ''
    return res
  },

  async derivePaths(source, target) {
    const res = await api.derivePaths(source, target)
    return res?.data || []
  },

  // ---------------------------------------------------------- 渲染 / 导出 / 入库
  async renderPath(pathId) {
    const res = await api.renderPath(pathId)
    const data = res?.data || {}
    state.lastRender = { pathId, ...data }
    state.renderResult = state.lastRender
    state.render = state.lastRender
    await actions.loadPaths()
    actions.toast('success', '长图与步骤文档已生成')
    return state.lastRender
  },

  async exportPath(pathId) {
    const res = await api.exportPath(pathId)
    const data = res?.data || {}
    state.lastRender = { pathId, ...data }
    state.renderResult = state.lastRender
    state.render = state.lastRender
    return state.lastRender
  },

  async publishPath(pathId) {
    const res = await api.publishPath(pathId)
    const data = res?.data || {}
    state.lastRender = { pathId, ...data }
    state.renderResult = state.lastRender
    state.render = state.lastRender
    await actions.loadPaths()
    actions.toast('success', data.message || '已推送到知识库')
    return data
  },

  // ---------------------------------------------------------- 知识库
  async checkHub() {
    try {
      const res = await api.hubStatus()
      const data = res?.data || {}
      state.hub.loggedIn = !!data.logged_in
      state.hub.username = data.username || ''
      state.hub.message = data.logged_in ? '知识库已连接' : '知识库未登录'
    } catch (err) {
      state.hub.loggedIn = false
      state.hub.message = err?.message || '知识库未连接'
    }
    return state.hub
  },

  async loginHub(username, password) {
    const res = await api.hubLogin(username, password)
    await actions.checkHub()
    actions.toast('success', '已登录知识库')
    return res
  },

  async loadModels() {
    try {
      const res = await api.models()
      const catalog = res?.data || {}
      state.models = actions.normalizeModels(catalog)
    } catch (err) {
      state.models = []
    }
    return state.models
  },

  /**
   * 知识库型号库有两种可能形状：
   *   1) { "扫地机": ["H5", "H6"], ... }          —— 知识库接口原样返回的字典
   *   2) [ { category: "扫地机", models: [...] } ] —— 前端下拉框用的列表
   * 统一转成第 2 种，组件里就不用再判断。
   */
  normalizeModels(catalog) {
    if (!catalog || typeof catalog !== 'object') return []
    if (Array.isArray(catalog)) {
      return catalog
        .map((entry) => ({
          category: entry?.category || entry?.name || '',
          models: Array.isArray(entry?.models) ? entry.models : [],
        }))
        .filter((entry) => entry.category)
    }
    return Object.entries(catalog).map(([category, models]) => ({
      category,
      models: Array.isArray(models) ? models : [],
    }))
  },

  // ---------------------------------------------------------- 选择
  selectNode(id) {
    state.selectedNodeId = id || ''
    if (id) state.selectedEdgeId = ''
  },

  selectEdge(id) {
    state.selectedEdgeId = id || ''
    if (id) state.selectedNodeId = ''
  },

  selectPath(id) {
    state.selectedPathId = id || ''
  },
}

export default { state, actions }
