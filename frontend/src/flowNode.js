// 自定义节点渲染模块：把 store 里的节点数据渲染成 LogicFlow HtmlNode 的真实 DOM。
// 与 FlowEditor.vue 解耦，单独放这里方便调样式和复用。
//
// 关键约束（来自 @logicflow/core 2.2.5 源码，已逐条核对）：
// 1. HtmlNodeModel.setAttributes 只认 properties.width / properties.height，所以卡片尺寸必须写在 properties 里。
// 2. HtmlNode.shouldUpdate() 比较的是 properties 的 JSON.stringify，
//    所以业务字段（标题/操作/截图/改版标记）也要放进 properties，改了才会触发 setHtml 重绘。
// 3. 节点 DOM 挂在 <foreignObject> 里，Vue 的 scoped 样式命中不到，
//    所以样式必须用 document.head 注入一份全局的。

export const NODE_TYPE = 'app-node'
export const NODE_WIDTH = 240
export const NODE_HEIGHT = 340

const STYLE_ID = 'ag-node-style'

export const NODE_CSS = `
.ag-node{width:${NODE_WIDTH}px;height:${NODE_HEIGHT}px;box-sizing:border-box;display:flex;flex-direction:column;background:#fff;border:1px solid #e5e8ec;border-radius:12px;box-shadow:0 2px 8px rgba(20,24,33,.08);overflow:hidden;position:relative;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;color:#1f2430;}
.ag-node--changed{border:2px solid #f0a020;box-shadow:0 2px 12px rgba(240,160,32,.28);}
.ag-head{flex:none;display:flex;align-items:center;gap:6px;padding:8px 10px;border-bottom:1px solid #f0f2f5;background:#fff;}
.ag-index{flex:none;width:20px;height:20px;border-radius:50%;background:#2b6cff;color:#fff;font-size:11px;display:flex;align-items:center;justify-content:center;}
.ag-title{flex:1;min-width:0;font-size:13px;font-weight:600;color:#1f2430;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.ag-badge{flex:none;font-size:10px;padding:1px 6px;border-radius:10px;color:#b06a00;background:#fff5e6;border:1px solid #ffd8a8;}
.ag-shot{flex:1 1 auto;min-height:0;background:#f0f2f5;display:flex;align-items:center;justify-content:center;overflow:hidden;}
.ag-shot__img{width:100%;height:100%;object-fit:contain;display:block;}
.ag-shot__empty{font-size:12px;color:#a9b0ba;}
.ag-info{flex:none;display:flex;gap:7px;align-items:flex-start;padding:6px 9px;font-size:11px;line-height:1.45;overflow:hidden;word-break:break-word;}
.ag-info__label{flex:none;font-size:10px;font-weight:600;line-height:16px;}
.ag-info__text{min-width:0;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;}
.ag-action{border-top:1px solid #e8efff;background:#f5f8ff;color:#344b76;}
.ag-action .ag-info__label{color:#2b6cff;}
.ag-note{border-top:1px solid #f1eadb;background:#fffaf0;color:#66563b;}
.ag-note .ag-info__label{color:#a56a16;}
.ag-footer{flex:none;display:flex;align-items:center;gap:8px;padding:0 10px;border-top:1px solid #f0f2f5;background:#fafbfc;}
.ag-btn{font-size:11px;line-height:1;padding:5px 8px;border-radius:5px;border:1px solid #dfe3e8;background:#fff;color:#4b5563;cursor:pointer;}
.ag-btn:hover{border-color:#2b6cff;color:#2b6cff;}
.ag-btn--danger{color:#e5484d;border-color:#f2c9cb;}
.ag-btn--danger:hover{background:#fdf2f3;}
`

function escapeHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** 素材图片地址：兼容后端 media 文件名与完整 URL。 */
export function mediaUrl(name) {
  if (!name) return ''
  const value = String(name)
  if (/^(https?:|data:|blob:)/i.test(value)) return value
  if (value.startsWith('/')) return value
  return `/media/${value}`
}

/**
 * 生成节点卡片的 HTML。
 * data-node-id / data-action 是给 FlowEditor 做事件委托用的钩子。
 */
export function buildNodeHtml(node = {}) {
  const id = escapeHtml(node.id || '')
  const title = escapeHtml(node.title || '未命名步骤')
  const action = escapeHtml(node.action || '（还没写操作说明）')
  const note = escapeHtml(node.description || '')
  const index = escapeHtml(node.index_label || '·')
  const shot = node.screenshot
    ? `<img class="ag-shot__img" src="${escapeHtml(mediaUrl(node.screenshot))}" alt="${title}" />`
    : '<div class="ag-shot__empty">拖入截图</div>'
  const badge = node.changed
    ? `<span class="ag-badge">已改版 ${Math.round((node.diff_ratio || 0) * 100)}%</span>`
    : ''
  const changedClass = node.changed ? ' ag-node--changed' : ''
  return `<div class="ag-node${changedClass}" data-node-id="${id}">
  <div class="ag-head">
    <span class="ag-index">${index}</span>
    <span class="ag-title" title="${title}">${title}</span>
    ${badge}
  </div>
  <div class="ag-shot">${shot}</div>
  <div class="ag-info ag-action" title="${action}">
    <span class="ag-info__label">操作</span>
    <span class="ag-info__text">${action}</span>
  </div>
  ${note ? `<div class="ag-info ag-note" title="${note}">
    <span class="ag-info__label">备注</span>
    <span class="ag-info__text">${note}</span>
  </div>` : ''}
  <div class="ag-footer">
    <button type="button" class="ag-btn" data-action="replace">替换截图</button>
    <button type="button" class="ag-btn ag-btn--danger" data-action="delete">删除</button>
  </div>
</div>`
}

/** 样式只注入一次，节点是 foreignObject 内容，不受 Vue scoped 样式影响。 */
export function injectNodeStyles() {
  if (typeof document === 'undefined') return
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = NODE_CSS
  document.head.appendChild(style)
}

/**
 * 组装 LogicFlow 节点的 properties。
 * 宽高与业务字段都放在这里 —— shouldUpdate 比较的正是 properties 的 JSON.stringify。
 */
export function buildNodeProperties(node = {}, indexLabel = '') {
  return {
    id: node.id || '',
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    title: node.title || '',
    action: node.action || '',
    description: node.description || '',
    screenshot: node.screenshot || '',
    screenshot_prev: node.screenshot_prev || '',
    changed: !!node.changed,
    diff_ratio: typeof node.diff_ratio === 'number' ? node.diff_ratio : 0,
    screenshot_version: node.screenshot_version || 1,
    index_label: indexLabel,
  }
}

/** 注册 app-node 自定义节点。 */
export function registerAppNode(lf) {
  lf.register(NODE_TYPE, ({ HtmlNode, HtmlNodeModel }) => {
    class AppNode extends HtmlNode {
      setHtml(rootEl) {
        const model = this.props.model
        const node = { ...(model?.properties || {}), id: model?.id }
        rootEl.innerHTML = buildNodeHtml(node)
        // 按钮事件必须在节点内部先拦截，否则 LogicFlow 会把点击当成拖动。
        if (!rootEl.__agBound) {
          rootEl.__agBound = true
          const stopCanvasEvent = (event) => {
            const target = event.target
            if (target && target.closest && target.closest('[data-action]')) {
              event.stopPropagation()
            }
          }
          rootEl.addEventListener('pointerdown', stopCanvasEvent)
          rootEl.addEventListener('mousedown', stopCanvasEvent)
          rootEl.addEventListener('click', (event) => {
            const button = event.target?.closest?.('[data-action]')
            if (!button) return
            event.preventDefault()
            event.stopPropagation()
            rootEl.dispatchEvent(new CustomEvent('ag-node-action', {
              bubbles: true,
              detail: {
                nodeId: rootEl.querySelector('[data-node-id]')?.getAttribute('data-node-id'),
                action: button.getAttribute('data-action'),
              },
            }))
          })
        }
      }
    }

    class AppNodeModel extends HtmlNodeModel {
      setAttributes() {
        super.setAttributes()
        // 兜底：即使 properties 里没写宽高，也保持卡片尺寸一致
        this.width = NODE_WIDTH
        this.height = NODE_HEIGHT
      }
    }

    return { view: AppNode, model: AppNodeModel }
  })
}
