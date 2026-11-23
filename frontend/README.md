# frontend — APP 操作指引工作台前端

Vue 3 + Vite 8 + LogicFlow 2.2.5 实现的三栏可视化路径画布。

## 启动

```bash
cd /Users/tuzi/个人项目/One/app_guide_workbench
./start.sh          # 同时启动后端(8095)与前端(5178)
```

浏览器打开 <http://localhost:5178/>。

只启动前端：

```bash
cd frontend && npm run dev
```

`vite.config.js` 已把 `/api` 与 `/media` 代理到 `http://127.0.0.1:8095`，所以组件里一律写相对路径。

## 目录

| 文件 | 作用 |
|---|---|
| `src/App.vue` | 三栏工作台布局（素材池 / 画布 / 路径面板）、顶栏、提示条 |
| `src/store.js` | 全局响应式状态与 actions（组件之间只通过它通信） |
| `src/api.js` | 后端接口封装，所有 `fetch` 集中在这里 |
| `src/style.css` | 全局基础样式（重置 + 滚动条），布局在 `App.vue` 里 |
| `src/flowNode.js` | 自定义 LogicFlow 节点：`app-node`（截图 + 标题 + 操作文字 + 改版徽标 + 按钮） |
| `src/components/FlowEditor.vue` | 画布本体：渲染、连线、拖拽建节点、工具栏 |
| `src/components/MaterialPool.vue` | 左侧截图素材池：文件选择、拖拽、Cmd/Ctrl+V 粘贴上传、删除；输入框内粘贴文字不被拦截 |
| `src/components/NodeInspector.vue` | 右侧节点属性：标题、操作文字、替换截图 |
| `src/components/PathPanel.vue` | 右侧路径面板：建路径、编步骤、填型号、重渲染、推送知识库 |

## 三个约定

1. **节点 DOM 在 `foreignObject` 里**，Vue 的 scoped 样式选不中它。节点样式统一由 `flowNode.js` 的 `injectNodeStyles()` 注入到 `document.head`（id `ag-node-style`）。
2. **LogicFlow 边必须用 `sourceNodeId` / `targetNodeId`**，写 `source` / `target` 会被 `pickEdgeConfig` 丢掉，连线画不出来。后端的事实源里仍是 `source` / `target`，转换在 `FlowEditor.vue` 的 `edgeToLf()` / `nodeToLf()` 里做。
3. **画布要跟着窗口自适应**。LogicFlow 自带的 `ResizeObserver` 只更新画布宽高，不会重算视图的缩放和平移，窗口一变大节点就会被裁到可视区外。`FlowEditor.vue` 里监听 `graph:resize` 事件并防抖调用 `lf.fitView(40, 40)` 重新适应；首次渲染后也调用一次。

## 布局说明

三栏用 CSS Grid 铺满整个窗口：左右两栏按 `clamp()` 随窗口宽度伸缩，中间画布吃掉全部剩余空间。整页不产生横向滚动条（`body { overflow: hidden }`），画布区域之外的滚动都在各栏内部完成。

完整说明与接口契约见电子脑洞仓库：
`/Volumes/ORICO/个人笔记记录/电子脑洞仓库/55_KnowBaseHub_8085/APP操作指引自动维护/`
