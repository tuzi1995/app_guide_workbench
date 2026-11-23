<script setup>
import { onMounted, ref, watch } from 'vue'
import { state, actions } from './store'
import FlowEditor from './components/FlowEditor.vue'
import MaterialPool from './components/MaterialPool.vue'
import NodeInspector from './components/NodeInspector.vue'
import PathPanel from './components/PathPanel.vue'

const activeRightPanel = ref('paths')

onMounted(() => {
  actions.init()
})

watch(
  () => state.selectedNodeId,
  (id) => {
    if (id) activeRightPanel.value = 'node'
  },
)
</script>

<template>
  <div class="workbench">
    <!-- 顶栏 -->
    <header class="topbar">
      <div class="tb-left">
        <h1 class="tb-title">APP 操作指引工作台</h1>
        <span class="tb-sub">可视化路径画布 · 局部改版自动重渲染 · 一键入库</span>
      </div>
      <div class="tb-right">
        <span class="tb-hub" :class="state.hub.loggedIn ? 'is-ok' : 'is-off'">
          知识库：{{ state.hub.loggedIn ? '已连接' : '未连接' }}
        </span>
        <button class="btn" type="button" @click="actions.checkHub()">检查连接</button>
        <button
          class="btn btn-primary"
          type="button"
          :disabled="state.saving"
          @click="actions.saveGraph()"
        >
          {{ state.saving ? '保存中…' : '保存画布' }}
        </button>
      </div>
    </header>

    <!-- 三栏主区 -->
    <main class="layout">
      <aside class="col col-left">
        <MaterialPool />
      </aside>

      <section class="col col-center">
        <FlowEditor />
      </section>

      <aside class="col col-right">
        <div class="right-tabs" role="tablist" aria-label="右侧面板">
          <button
            class="right-tab"
            :class="{ 'is-active': activeRightPanel === 'paths' }"
            type="button"
            role="tab"
            :aria-selected="activeRightPanel === 'paths'"
            @click="activeRightPanel = 'paths'"
          >功能路径</button>
          <button
            class="right-tab"
            :class="{ 'is-active': activeRightPanel === 'node' }"
            type="button"
            role="tab"
            :aria-selected="activeRightPanel === 'node'"
            @click="activeRightPanel = 'node'"
          >节点属性</button>
        </div>
        <div v-show="activeRightPanel === 'paths'" class="right-panel-content">
          <PathPanel />
        </div>
        <div v-show="activeRightPanel === 'node'" class="right-panel-content">
          <NodeInspector />
        </div>
      </aside>
    </main>

    <!-- 提示消息 -->
    <div class="toasts">
      <div
        v-for="t in state.toasts"
        :key="t.id"
        class="toast"
        :class="`toast-${t.type}`"
        @click="actions.dismissToast(t.id)"
      >
        {{ t.message }}
      </div>
    </div>

    <!-- 后端没起来时的显眼提示 -->
    <div v-if="!state.backendOnline && !state.loading" class="offline">
      <p class="offline-title">连不上后端服务</p>
      <p class="offline-body">
        请在项目目录执行：<code>cd backend && .venv/bin/python app.py</code><br />
        然后刷新本页。后端默认端口是 8095。
      </p>
    </div>
  </div>
</template>

<style scoped>
.workbench {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  background: #f4f6f9;
  color: #1f2430;
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Hiragino Sans GB',
    'Microsoft YaHei', sans-serif;
}

/* ---------------- 顶栏 ---------------- */
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 18px;
  background: #fff;
  border-bottom: 1px solid #e5e8ec;
  flex: 0 0 auto;
}

.tb-title {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
}

.tb-sub {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  color: #8a9099;
}

.tb-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.tb-hub {
  font-size: 12px;
  padding: 3px 9px;
  border-radius: 20px;
}

.tb-hub.is-ok {
  background: #e8f7ee;
  color: #1a7f43;
}

.tb-hub.is-off {
  background: #fdeeee;
  color: #c0392b;
}

/* ---------------- 三栏 ---------------- */
/* 左右两栏固定宽度，中间画布吃掉全部剩余空间，整块铺满窗口 */
.layout {
  flex: 1 1 auto;
  display: grid;
  grid-template-columns: clamp(220px, 18vw, 300px) minmax(0, 1fr) clamp(300px, 23vw, 400px);
  gap: 12px;
  padding: 12px;
  min-height: 0;
}

.col {
  background: #fff;
  border-radius: 10px;
  border: 1px solid #e5e8ec;
  overflow: auto;
  min-height: 0;
  min-width: 0;
}

/* 右栏使用页签切换，避免路径与节点属性上下挤压。 */
.col-right {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.right-tabs {
  display: flex;
  flex: 0 0 auto;
  gap: 4px;
  padding: 10px 10px 0;
  border-bottom: 1px solid #e5e8ec;
  background: #fff;
}

.right-tab {
  border: 0;
  border-bottom: 2px solid transparent;
  padding: 8px 12px 9px;
  background: transparent;
  color: #7b8491;
  font-size: 14px;
  cursor: pointer;
}

.right-tab:hover { color: #2b6cff; }
.right-tab.is-active {
  border-bottom-color: #2b6cff;
  color: #1f2430;
  font-weight: 600;
}

.right-panel-content {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.right-panel-content > :first-child { min-height: 100%; }

/* 左栏素材池：上传区固定，缩略图列表吃掉剩余高度并自己滚动 */
.col-left {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.col-left > * {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.col-center {
  padding: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ---------------- 按钮 ---------------- */
.btn {
  border: 1px solid #dfe3e8;
  background: #fff;
  color: #1f2430;
  border-radius: 8px;
  padding: 6px 12px;
  font-size: 13px;
  cursor: pointer;
}

.btn:hover:not(:disabled) {
  border-color: #2b6cff;
  color: #2b6cff;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-primary {
  background: #2b6cff;
  border-color: #2b6cff;
  color: #fff;
}

.btn-primary:hover:not(:disabled) {
  background: #1f5ae0;
  color: #fff;
}

/* ---------------- 提示 ---------------- */
.toasts {
  position: fixed;
  right: 18px;
  bottom: 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 99;
}

.toast {
  padding: 9px 14px;
  border-radius: 8px;
  font-size: 13px;
  color: #fff;
  cursor: pointer;
  box-shadow: 0 4px 14px rgba(20, 24, 33, 0.16);
  max-width: 360px;
}

.toast-success { background: #1a7f43; }
.toast-error { background: #c0392b; }
.toast-warn { background: #b7791f; }
.toast-info { background: #2b6cff; }

/* ---------------- 离线提示 ---------------- */
.offline {
  position: fixed;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  background: #fff;
  border: 1px solid #f0bcbc;
  border-radius: 12px;
  padding: 20px 24px;
  box-shadow: 0 12px 40px rgba(20, 24, 33, 0.18);
  z-index: 100;
  max-width: 460px;
}

.offline-title {
  margin: 0 0 8px;
  font-size: 16px;
  font-weight: 700;
  color: #c0392b;
}

.offline-body {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: #4a5160;
}

.offline code {
  background: #f4f6f9;
  border-radius: 5px;
  padding: 2px 6px;
  font-size: 12px;
}
</style>
