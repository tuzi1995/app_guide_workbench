// 编译并执行真实 Vue 面板，覆盖刷新预览、切换路径与异步返回顺序。
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { registerHooks } from 'node:module'
import { afterEach, beforeEach, test } from 'node:test'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { createSSRApp, effectScope, nextTick, proxyRefs } from 'vue'
import { renderToString } from 'vue/server-renderer'

const storeUrl = new URL('../src/store.js', import.meta.url).href
registerHooks({ resolve(specifier, context, nextResolve) {
  if (context.parentURL === storeUrl && specifier === './api') specifier = './api.js'
  return nextResolve(specifier, context)
} })
const { state, actions } = await import(storeUrl)
const { descriptor } = parse(await readFile(new URL('../src/components/PathPanel.vue', import.meta.url), 'utf8'))
const script = compileScript(descriptor, { id: 'test-panel', genDefaultAs: 'component' })
const template = compileTemplate({ source: descriptor.template.content, id: 'test-panel',
  compilerOptions: { bindingMetadata: script.bindings } })
assert.deepEqual(template.errors, [])
const source = `${script.content}\n${template.code.replace('export function render', 'function render')}\ncomponent.render = render; export default component;`
  .replaceAll("from 'vue'", `from '${import.meta.resolve('vue')}'`)
  .replaceAll('from "vue"', `from '${import.meta.resolve('vue')}'`)
  .replace("from '../store'", `from '${storeUrl}'`)
  .replace("from './AnnotationEditor.vue'", "from 'data:text/javascript,export default {}'")
const { default: component } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
const originalFetch = globalThis.fetch
let scope, panel
const p1 = { id: 'p1', title: '路径一', node_ids: [], render_version: 1, long_image_url: '/media/exports/p1/1/long.png' }
const p2 = { id: 'p2', title: '路径二', node_ids: [], render_version: 0 }

beforeEach(() => {
  state.graph.paths = structuredClone([p1, p2])
  state.selectedPathId = 'p1'
  state.lastRender = state.renderResult = state.render = null
  globalThis.fetch = async () => Response.json({ success: true, data: {
    version: 1, markdown: '# 已保存的文档', long_image_url: p1.long_image_url,
  } })
  scope = effectScope()
  panel = scope.run(() => component.setup({}, { expose() {} }))
})
afterEach(() => { scope.stop(); globalThis.fetch = originalFetch })

function findButton(vnode, label) {
  if (vnode?.type === 'button' && typeof vnode.children === 'string' && vnode.children.includes(label)) return vnode
  for (const child of Array.isArray(vnode?.children) ? vnode.children : []) {
    const found = findButton(child, label)
    if (found) return found
  }
}
async function previewButton() {
  let vnode
  await renderToString(createSSRApp({ render() {
    vnode = component.render({}, [], {}, proxyRefs(panel), {}, {})
    return vnode
  } }))
  return findButton(vnode, '查看生成结果')
}

test('刷新后已有生成结果的路径可以查看，且读取已保存文档', async () => {
  const button = await previewButton()
  assert.ok(button)
  assert.equal(Boolean(button.props.disabled), false, '已有结果但查看按钮仍为禁用')
  await button.props.onClick()
  assert.equal(panel.renderResult.value.markdown, '# 已保存的文档')
  assert.equal(panel.showResult.value, true)
})

test('尚未生成的路径禁用查看按钮', async () => {
  actions.selectPath('p2')
  await nextTick()
  assert.equal(Boolean((await previewButton()).props.disabled), true)
})

test('自动切换路径也清空旧结果，避免将上一条路径结果显示在新路径下', async () => {
  panel.renderResult.value = { markdown: '路径一的旧结果' }
  panel.showResult.value = true
  actions.selectPath('p2')
  await nextTick()
  assert.equal(panel.renderResult.value, null)
  assert.equal(panel.showResult.value, false)
})

test('渲染尚未返回时切换路径，不将返回结果显示在另一条路径下', async () => {
  const originalRender = actions.renderPath
  let complete
  actions.renderPath = () => new Promise(resolve => { complete = resolve })
  try {
    const pending = panel.doRender(state.graph.paths[0])
    actions.selectPath('p2')
    await nextTick()
    complete({ markdown: '路径一的返回结果', long_image_url: p1.long_image_url })
    await pending
    assert.equal(panel.renderResult.value, null)
    assert.equal(panel.showResult.value, false)
  } finally { actions.renderPath = originalRender }
})

test('切换品类时清除不属于新品类的型号', async () => {
  state.models = [{ category: '旧品类', models: ['旧型号'] }, { category: '新品类', models: ['新型号'] }]
  Object.assign(state.graph.paths[0], { product_category_name: '旧品类', product_name: '旧型号' })
  await nextTick()
  const originalUpdate = actions.updatePath
  const patches = []
  actions.updatePath = async (_id, patch) => { patches.push(patch) }
  try {
    panel.fieldForm.value.product_category_name = '新品类'
    panel.onCategoryChange()
    assert.equal(panel.fieldForm.value.product_name, '')
    assert.ok(patches.some(p => p.product_name === ''))
  } finally { actions.updatePath = originalUpdate }
})

test('本地生成了更新版本时，不能继续将它标成已入库', () => {
  const path = { ...p1, kb_published_at: 'test', kb_published_version: 1 }
  assert.equal(panel.badgeOf(path).text, '已入库')
  path.render_version = 2
  assert.equal(panel.badgeOf(path).text, '已生成，待同步知识库')
})
