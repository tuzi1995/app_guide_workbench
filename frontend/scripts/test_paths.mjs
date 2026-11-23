// 真实 store + API 调用链；用内存后端隔离正式画布数据。
import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { afterEach, beforeEach, test } from 'node:test'

const storeUrl = new URL('../src/store.js', import.meta.url).href
registerHooks({
  resolve(specifier, context, nextResolve) {
    // Vite 支持省略扩展名；让 Node 加载同一个源文件。
    if (context.parentURL === storeUrl && specifier === './api') specifier = './api.js'
    return nextResolve(specifier, context)
  },
})
const { state, actions } = await import(storeUrl)
const originalFetch = globalThis.fetch
let serverGraph

beforeEach(async () => {
  serverGraph = { title: '测试画布', nodes: [], edges: [], paths: [{ id: 'p1', title: '已有路径', node_ids: [] }] }
  state.selectedPathId = ''
  globalThis.fetch = async (url, options = {}) => {
    const method = options.method || 'GET'
    const body = options.body ? JSON.parse(options.body) : null
    let data
    if (url === '/api/graph' && method === 'GET') data = serverGraph
    else if (url === '/api/graph' && method === 'PUT') {
      serverGraph = { ...serverGraph, ...body }
      data = { saved_at: 'test' }
    } else if (url === '/api/paths' && method === 'GET') data = serverGraph.paths
    else if (url === '/api/paths' && method === 'POST') {
      data = { ...body, id: 'p2', stale: true }
      serverGraph.paths.push(data)
    } else if (url.startsWith('/api/paths/') && method === 'DELETE') {
      serverGraph.paths = serverGraph.paths.filter(p => p.id !== url.split('/').at(-1))
      data = {}
    } else throw new Error(`意外请求: ${method} ${url}`)
    return Response.json({ success: true, data })
  }
  await actions.loadGraph()
  await actions.loadPaths()
})

afterEach(() => {
  globalThis.fetch = originalFetch
})

test('创建后新路径立即出现在面板读取的列表中', async () => {
  const existing = state.graph.paths[0]
  const created = await actions.addPath({ title: '新功能路径', node_ids: [] })
  assert.ok(state.graph.paths.some(p => p.id === created.id), '创建成功，但右侧列表缺少新路径')
  assert.equal(state.graph.paths[0], existing, '刷新时保留已有路径对象')
})

test('创建后自动选中新路径以便继续编辑', async () => {
  const created = await actions.addPath({ title: '新功能路径', node_ids: [] })
  assert.equal(state.selectedPathId, created.id)
})

test('创建后保存画布、重新加载仍能找到新路径', async () => {
  const created = await actions.addPath({ title: '新功能路径', node_ids: [] })
  await actions.saveGraph()
  await actions.loadGraph()
  assert.ok(state.graph.paths.some(p => p.id === created.id), '保存画布覆盖了刚创建的新路径')
})

test('删除路径后立即从面板列表移除并清空选择', async () => {
  actions.selectPath('p1')
  await actions.removePath('p1')
  assert.deepEqual(state.graph.paths, [])
  assert.equal(state.selectedPathId, '')
})

test('刷新同步服务端新增、删除与更新的路径', async () => {
  actions.selectPath('p1')
  serverGraph.paths = [{ id: 'p3', title: '其他页面新增的路径', node_ids: [] }]
  await actions.loadPaths()
  assert.deepEqual(state.graph.paths.map(p => p.id), ['p3'])
  assert.equal(state.selectedPathId, '')
  serverGraph.paths[0].title = '已修改名称'
  await actions.loadPaths()
  assert.equal(state.graph.paths[0].title, '已修改名称')
})

test('旧页面保存画布不会覆盖服务端新增路径和发布记录', async () => {
  serverGraph.paths.push({ id: 'p3', title: '另一个页面新增', node_ids: [] })
  serverGraph.paths[0].kb_managed_image_urls = ['http://example.test/new.png']
  await actions.saveGraph()
  assert.ok(serverGraph.paths.some(p => p.id === 'p3'), '旧页面把其他页面的新路径覆盖了')
  assert.deepEqual(serverGraph.paths[0].kb_managed_image_urls, ['http://example.test/new.png'])
})

test('保存画布会等待尚未完成的编辑请求', async () => {
  const previousFetch = globalThis.fetch
  let release
  const gate = new Promise(resolve => { release = resolve })
  let checkpointSent = false
  globalThis.fetch = async (url, options) => {
    if (url === '/api/paths' && options.method === 'POST') await gate
    if (url === '/api/graph' && options.method === 'PUT') checkpointSent = true
    return previousFetch(url, options)
  }
  const creating = actions.addPath({ title: '仍在提交的路径', node_ids: [] })
  const saving = actions.saveGraph()
  await new Promise(setImmediate)
  const sentBeforeEdit = checkpointSent
  release()
  await Promise.all([creating, saving])
  assert.equal(sentBeforeEdit, false, '编辑请求尚未完成就提示保存成功')
  assert.equal(checkpointSent, true)
  assert.ok(serverGraph.paths.some(p => p.id === 'p2'))
})
