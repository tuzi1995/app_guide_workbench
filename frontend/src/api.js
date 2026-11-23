// 后端接口封装：所有网络请求集中在这里，组件层只调用 api.*。
// 后端默认 http://127.0.0.1:8095，开发时由 vite.config.js 代理 /api 与 /media。

const BASE = ''

async function sendRequest(path, { method = 'GET', body, headers = {}, raw = false } = {}) {
  const opts = { method, headers: { ...headers } }
  if (body instanceof FormData) {
    opts.body = body
  } else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }

  let resp
  try {
    resp = await fetch(BASE + path, opts)
  } catch (err) {
    throw new Error('连不上后端服务，请确认后端已在 8095 端口启动（' + err.message + '）')
  }

  if (raw) return resp

  let data = null
  const text = await resp.text()
  if (text) {
    try {
      data = JSON.parse(text)
    } catch (err) {
      data = { success: false, message: text.slice(0, 300) }
    }
  }

  if (!resp.ok || (data && data.success === false)) {
    const msg = (data && data.message) || `请求失败（HTTP ${resp.status}）`
    const e = new Error(msg)
    e.status = resp.status
    e.payload = data
    throw e
  }
  return data
}

const pendingWrites = new Set()

function request(path, options = {}) {
  const pending = sendRequest(path, options)
  if (options.method && options.method !== 'GET') {
    pendingWrites.add(pending)
    pending.then(() => pendingWrites.delete(pending), () => pendingWrites.delete(pending))
  }
  return pending
}

export const api = {
  health: () => request('/api/health'),

  getGraph: () => request('/api/graph'),
  saveGraph: async () => {
    // 各项编辑已由专用接口自动保存；先等正在提交的编辑，再确认最新画布。
    // 不用当前页面的旧快照覆盖服务端，避免覆盖其他页面或自动渲染的更新。
    await Promise.all([...pendingWrites])
    return request('/api/graph', { method: 'PUT', body: {} })
  },

  createNode: (node) => request('/api/nodes', { method: 'POST', body: node }),
  updateNode: (id, patch) => request(`/api/nodes/${id}`, { method: 'PATCH', body: patch }),
  deleteNode: (id) => request(`/api/nodes/${id}`, { method: 'DELETE' }),

  createEdge: (edge) => request('/api/edges', { method: 'POST', body: edge }),
  deleteEdge: (id) => request(`/api/edges/${id}`, { method: 'DELETE' }),

  listPaths: () => request('/api/paths'),
  createPath: (path) => request('/api/paths', { method: 'POST', body: path }),
  updatePath: (id, patch) => request(`/api/paths/${id}`, { method: 'PATCH', body: patch }),
  deletePath: (id) => request(`/api/paths/${id}`, { method: 'DELETE' }),
  derivePaths: (source, target) =>
    request('/api/paths/derive', { method: 'POST', body: { source, target } }),

  // 局部改版的核心接口：只替换一个节点的截图，后端自动沿连线重渲染受影响路径
  uploadNodeScreenshot: (nodeId, file) => {
    const fd = new FormData()
    fd.append('file', file)
    return request(`/api/nodes/${nodeId}/screenshot`, { method: 'POST', body: fd })
  },

  uploadMedia: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return request('/api/media', { method: 'POST', body: fd })
  },
  listMedia: () => request('/api/media'),
  deleteMedia: (filename) => request(`/api/media/${filename}`, { method: 'DELETE' }),

  renderPath: (id) => request(`/api/paths/${id}/render`, { method: 'POST' }),
  exportPath: (id) => request(`/api/paths/${id}/export`),
  publishPath: (id) => request(`/api/paths/${id}/publish`, { method: 'POST' }),

  hubStatus: () => request('/api/hub/status'),
  hubLogin: (username, password) =>
    request('/api/hub/login', { method: 'POST', body: { username, password } }),
  models: () => request('/api/models'),
}

export default api
