<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { state, actions } from '../store'
import * as apiNamespace from '../api'

defineOptions({ name: 'MaterialPool' })

// api.js 的导出形态（具名导出 `api` / default 对象 / 散函数）由后端同学最终决定，
// 这里按优先级依次探测，避免因为导出风格不同导致整页白屏。
const api =
  apiNamespace.default && typeof apiNamespace.default.deleteMedia === 'function'
    ? apiNamespace.default
    : apiNamespace.api && typeof apiNamespace.api.deleteMedia === 'function'
      ? apiNamespace.api
      : apiNamespace

const media = computed(() => state.media || [])
const uploading = ref(false)
const dragOver = ref(false)
const fileInput = ref(null)
// 记录最近一次 dragstart 的时间，用来区分「拖拽」和「点击」两种手势：
// 拖完松手浏览器还会补一个 click，如果不加判断就会误触发「替换节点截图」。
let lastDragAt = 0
// dragenter/dragleave 在子元素之间来回冒泡，用计数器判断是否真的离开了拖拽区
let dragDepth = 0

/** 把媒体文件名转换成可访问的 URL；已经是完整地址的直接原样返回 */
function mediaUrl(name) {
  if (!name) return ''
  if (/^(https?:|data:|blob:)/i.test(name)) return name
  if (name.startsWith('/media/')) return name
  return `/media/${name}`
}

/** 只在确实拖入了「文件」时才当作上传，拖拽我们自己的缩略图不应触发上传 */
function hasFiles(event) {
  const types = event.dataTransfer?.types
  return !!types && Array.from(types).includes('Files')
}

function pickFile() {
  fileInput.value?.click()
}

async function uploadFiles(fileList) {
  const files = Array.from(fileList || []).filter((f) => f && f.type.startsWith('image/'))
  if (!files.length) {
    actions.toast('error', '请选择图片文件（PNG / JPG）')
    return
  }
  uploading.value = true
  try {
    for (const file of files) {
      // 契约：actions.uploadMedia(file) 返回新素材对象
      const res = await actions.uploadMedia(file)
      const item = res?.data || res
      // 如果 store 没有自动把新素材塞进 state.media，这里兜底刷一次列表
      if (item?.filename && !media.value.some((m) => m.filename === item.filename)) {
        actions.loadMedia?.()
      }
    }
    actions.toast('success', `已上传 ${files.length} 张截图`)
  } catch (err) {
    actions.toast('error', `上传失败：${err?.message || err}`)
  } finally {
    uploading.value = false
  }
}

function onPick(event) {
  const input = event.target
  uploadFiles(input.files)
  // 重置 value，否则连续选同一个文件不会触发 change
  input.value = ''
}

function onDragEnter(event) {
  if (!hasFiles(event)) return
  dragDepth += 1
  dragOver.value = true
}

function onDragOver(event) {
  if (!hasFiles(event)) return
  event.dataTransfer.dropEffect = 'copy'
  dragOver.value = true
}

function onDragLeave() {
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) dragOver.value = false
}

function onDrop(event) {
  if (!hasFiles(event)) return
  dragDepth = 0
  dragOver.value = false
  uploadFiles(event.dataTransfer.files)
}

/**
 * 复制截图后在页面任意非编辑区域按 Cmd/Ctrl+V，直接上传到素材池。
 * 正在输入框里粘贴文字时不拦截浏览器默认行为；剪贴板没有图片时也照常放行。
 */
function onPaste(event) {
  const target = event.target
  const isEditing = target instanceof Element
    && !!target.closest('input, textarea, [contenteditable="true"]')
  if (isEditing) return

  const items = Array.from(event.clipboardData?.items || [])
  const files = items
    .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
    .map((item) => item.getAsFile())
    .filter(Boolean)
  if (!files.length) return

  event.preventDefault()
  const now = new Date()
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
    '_',
    String(now.getHours()).padStart(2, '0'),
    String(now.getMinutes()).padStart(2, '0'),
    String(now.getSeconds()).padStart(2, '0'),
  ].join('')
  const namedFiles = files.map((file, index) => {
    const extension = (file.type.split('/')[1] || 'png').replace('jpeg', 'jpg')
    return new File([file], `粘贴截图_${stamp}_${index + 1}.${extension}`, {
      type: file.type,
      lastModified: Date.now(),
    })
  })
  uploadFiles(namedFiles)
}

onMounted(() => window.addEventListener('paste', onPaste))
onBeforeUnmount(() => window.removeEventListener('paste', onPaste))

/** 缩略图拖到画布：写约定好的自定义 MIME，画布那边监听 drop 后能直接拿到文件名 */
function onDragStart(event, item) {
  lastDragAt = Date.now()
  const payload = {
    filename: item.filename,
    url: item.url || mediaUrl(item.filename),
    title: '',
    action: '',
  }
  event.dataTransfer.effectAllowed = 'copy'
  event.dataTransfer.setData('application/x-app-shot', JSON.stringify(payload))
  // 有些浏览器要求必须 setData 至少一种文本类型才允许拖拽
  event.dataTransfer.setData('text/plain', item.filename)
}

function onDragEnd() {
  lastDragAt = Date.now()
}

/** 点击缩略图：把截图填到当前选中的节点上 */
async function onThumbClick(item) {
  if (Date.now() - lastDragAt < 300) return // 刚刚拖拽过，忽略这次 click
  const selectedId = state.selectedNodeId
  if (!selectedId) {
    actions.toast('info', '请先在画布上选中一个节点')
    return
  }
  try {
    await actions.updateNode(selectedId, { screenshot: item.filename })
    actions.toast('success', '已替换当前节点截图')
  } catch (err) {
    // store 已提示失败并回滚节点，不能提前显示替换成功。
  }
}

async function onDelete(item) {
  if (!window.confirm(`确认删除素材「${item.filename}」？该操作不可恢复。`)) return
  try {
    await api.deleteMedia(item.filename)
    actions.loadMedia?.()
    actions.toast('success', '素材已删除')
  } catch (err) {
    actions.toast('error', `删除失败：${err?.message || err}`)
  }
}
</script>

<template>
  <section class="material-pool">
    <header class="mp-head">
      <h2>截图素材池</h2>
      <span class="mp-count">{{ media.length }} 张</span>
    </header>

    <div class="mp-actions">
      <button class="btn btn-primary" type="button" :disabled="uploading" @click="pickFile">
        {{ uploading ? '上传中…' : '上传截图' }}
      </button>
      <input
        ref="fileInput"
        class="hidden-input"
        type="file"
        accept="image/*"
        multiple
        @change="onPick"
      />
    </div>

    <div
      class="mp-drop"
      :class="{ 'is-over': dragOver }"
      @dragenter.prevent="onDragEnter"
      @dragover.prevent="onDragOver"
      @dragleave.prevent="onDragLeave"
      @drop.prevent="onDrop"
    >
      <p class="mp-drop-title">拖拽图片到这里，或直接粘贴截图</p>
      <p class="mp-drop-hint">复制截图后按 Cmd / Ctrl + V，也支持一次拖入多张</p>
    </div>

    <p v-if="!media.length" class="mp-empty">素材池还是空的，先上传几张截图吧。</p>

    <ul v-else class="mp-grid">
      <li
        v-for="item in media"
        :key="item.filename"
        class="mp-cell"
        draggable="true"
        @dragstart="onDragStart($event, item)"
        @dragend="onDragEnd"
        @click="onThumbClick(item)"
      >
        <div class="mp-thumb">
          <img :src="item.url || mediaUrl(item.filename)" :alt="item.title || item.filename" loading="lazy" />
          <button
            class="mp-del"
            type="button"
            title="删除素材"
            @click.stop="onDelete(item)"
          >
            ×
          </button>
        </div>
        <span class="mp-name" :title="item.filename">{{ item.filename }}</span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.material-pool {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  text-align: left;
  color: #1f2430;
  font-size: 14px;
  line-height: 1.5;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

.mp-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  flex: none;
}

.mp-head h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #1f2430;
}

.mp-count {
  font-size: 12px;
  color: #8a9099;
}

.mp-actions {
  display: flex;
  gap: 8px;
  flex: none;
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

.hidden-input {
  display: none;
}

.mp-drop {
  border: 1px dashed #c9d0da;
  border-radius: 10px;
  background: #fafbfc;
  padding: 14px 10px;
  text-align: center;
  transition: border-color 0.15s, background 0.15s;
  flex: none;
}

.mp-drop.is-over {
  border-color: #2b6cff;
  background: #f2f6ff;
}

.mp-drop-title {
  margin: 0;
  font-size: 13px;
  color: #4a5160;
}

.mp-drop-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: #9aa1ab;
}

.mp-empty {
  margin: 0;
  font-size: 13px;
  color: #9aa1ab;
}

.mp-grid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(92px, 1fr));
  gap: 10px;
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  align-content: start;
}

.mp-cell {
  cursor: grab;
  border-radius: 8px;
  padding: 4px;
  transition: background 0.15s;
}

.mp-cell:hover {
  background: #f5f7fa;
}

.mp-cell:active {
  cursor: grabbing;
}

.mp-thumb {
  position: relative;
  border-radius: 6px;
  overflow: hidden;
  background: #f0f2f5;
  aspect-ratio: 3 / 4;
}

.mp-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.mp-del {
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 20px;
  height: 20px;
  line-height: 1;
  border: none;
  border-radius: 50%;
  background: rgba(20, 24, 33, 0.62);
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}

.mp-del:hover {
  background: #e5484d;
}

.mp-name {
  display: block;
  margin-top: 4px;
  font-size: 11px;
  color: #8a9099;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
