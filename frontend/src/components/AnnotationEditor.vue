<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
const props = defineProps({ imageUrl: String, title: String, annotations: { type: Array, default: () => [] }, saving: Boolean })
const emit = defineEmits(['save', 'close'])
const marks = ref(JSON.parse(JSON.stringify(props.annotations)))
const tool = ref('rect')
const text = ref('点击这里')
const color = ref('#ef3038')
const stage = ref(null)
const ratio = ref(1)
const imageWidth = ref(1000)
const loaded = ref(false)
const imageError = ref(false)
const dialog = ref(null)
onMounted(async () => { await nextTick(); dialog.value?.focus() })
function imageLoaded(event) {
  imageWidth.value = event.target.naturalWidth
  ratio.value = event.target.naturalHeight / imageWidth.value
  loaded.value = true
  imageError.value = false
}
function close() { if (!props.saving) emit('close') }
function stroke(mark) { return Math.max(1, mark.strokeWidth || 4) * 1000 / imageWidth.value }
const draft = ref(null)
const start = ref(null)
watch(() => props.annotations, value => { marks.value = JSON.parse(JSON.stringify(value || [])) })
const visible = computed(() => draft.value ? [...marks.value, draft.value] : marks.value)
function point(event) {
  const box = stage.value.getBoundingClientRect()
  return { x: Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)), y: Math.max(0, Math.min(1, (event.clientY - box.top) / box.height)) }
}
function down(event) {
  if (props.saving || !loaded.value || event.button !== 0) return
  const p = point(event)
  if (tool.value === 'text') {
    if (text.value.trim()) marks.value.push({ type: 'text', ...p, text: text.value.trim(), color: color.value, fontSize: 0.045 })
    return
  }
  start.value = p
  stage.value.setPointerCapture(event.pointerId)
}
function move(event) {
  if (!start.value) return
  const p = point(event), s = start.value
  draft.value = tool.value === 'arrow'
    ? { type: 'arrow', x: s.x, y: s.y, x2: p.x, y2: p.y, color: color.value, strokeWidth: 4 }
    : { type: 'rect', x: Math.min(s.x,p.x), y: Math.min(s.y,p.y), width: Math.abs(s.x-p.x), height: Math.abs(s.y-p.y), color: color.value, strokeWidth: 4 }
}
function up(event) {
  if (!start.value) return
  move(event)
  if (draft.value && (draft.value.type === 'arrow'
    ? Math.hypot(draft.value.x2 - draft.value.x, draft.value.y2 - draft.value.y) > .005
    : draft.value.width > .005 && draft.value.height > .005)) marks.value.push(draft.value)
  if (stage.value.hasPointerCapture(event.pointerId)) stage.value.releasePointerCapture(event.pointerId)
  draft.value = null; start.value = null
}
function arrowHead(mark) {
  const x = mark.x2 * 1000, y = mark.y2 * 1000 * ratio.value
  const angle = Math.atan2(y - mark.y * 1000 * ratio.value, x - mark.x * 1000)
  const length = Math.max(10, (mark.strokeWidth || 4) * 4) * 1000 / imageWidth.value
  return `${x},${y} ${x-length*Math.cos(angle-.45)},${y-length*Math.sin(angle-.45)} ${x-length*Math.cos(angle+.45)},${y-length*Math.sin(angle+.45)}`
}
</script>

<template>
  <Teleport to="body">
    <div class="annotation-backdrop" @keydown.esc="close">
      <section ref="dialog" tabindex="-1" class="annotation-dialog" role="dialog" aria-modal="true" aria-label="路径专属截图标注">
        <header><strong>{{ title }} · 路径专属标注</strong><button @click="close()" :disabled="saving">关闭</button></header>
        <div class="annotation-tools">
          <button v-for="item in [['rect','红框'],['arrow','箭头'],['text','文字']]" :key="item[0]" :class="{active:tool===item[0]}" @click="tool=item[0]">{{ item[1] }}</button>
          <input type="color" v-model="color" aria-label="标注颜色" />
          <input v-if="tool==='text'" v-model="text" placeholder="输入标注文字，再点击截图放置" maxlength="120" />
          <button @click="marks.pop()" :disabled="!marks.length || saving">撤销</button>
          <button @click="marks=[]" :disabled="!marks.length || saving">清空</button>
        </div>
        <p>红框/箭头：在图上拖动；文字：输入后点击放置。仅当前路径生效，原图不会修改；换图后需重新标注。</p>
        <p v-if="imageError" role="alert">截图加载失败，请关闭后检查节点图片。</p>
        <div class="annotation-scroll">
          <div class="annotation-stage" ref="stage" @pointerdown.prevent="down" @pointermove="move" @pointerup="up" @pointercancel="start=null;draft=null">
            <img :src="imageUrl" alt="原始截图" draggable="false" @load="imageLoaded" @error="imageError = true; loaded = false" />
            <svg :viewBox="`0 0 1000 ${1000*ratio}`" preserveAspectRatio="none">
              <template v-for="(mark,i) in visible" :key="i">
                <rect v-if="mark.type==='rect'" :x="mark.x*1000" :y="mark.y*1000*ratio" :width="mark.width*1000" :height="mark.height*1000*ratio" fill="none" :stroke="mark.color" :stroke-width="stroke(mark)" />
                <template v-else-if="mark.type==='arrow'">
                  <line :x1="mark.x*1000" :y1="mark.y*1000*ratio" :x2="mark.x2*1000" :y2="mark.y2*1000*ratio" :stroke="mark.color" :stroke-width="stroke(mark)" />
                  <polygon :points="arrowHead(mark)" :fill="mark.color" />
                </template>
                <text v-else-if="mark.type==='text'" :x="mark.x*1000" :y="mark.y*1000*ratio" dominant-baseline="hanging" :fill="mark.color" :font-size="mark.fontSize*1000" font-weight="700">{{ mark.text }}</text>
              </template>
            </svg>
          </div>
        </div>
        <footer><span>{{ marks.length }} 个标注</span><button @click="close()" :disabled="saving">取消</button><button class="save" :disabled="saving || !loaded || !!start" @click="emit('save',marks)">{{ saving ? '保存中…' : '保存标注' }}</button></footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.annotation-backdrop{position:fixed;inset:0;z-index:2000;background:#142033b8;display:flex;align-items:center;justify-content:center;padding:20px}
.annotation-dialog{display:flex;flex-direction:column;width:min(960px,96vw);height:94vh;background:#fff;border-radius:12px;overflow:hidden;color:#263245}
header,footer{display:flex;align-items:center;gap:12px;padding:12px 18px;border-bottom:1px solid #e1e6ed}header strong,footer span{flex:1}footer{border-top:1px solid #e1e6ed;border-bottom:0}
.annotation-tools{display:flex;gap:8px;align-items:center;padding:12px 18px;flex-wrap:wrap}button{border:1px solid #d3dae5;background:#fff;border-radius:6px;padding:7px 12px;cursor:pointer}button.active,.save{background:#2563eb;color:#fff;border-color:#2563eb}button:disabled{opacity:.5;cursor:default}input[type=text],input:not([type]){padding:8px;border:1px solid #d3dae5;border-radius:6px;min-width:240px}input[type=color]{width:36px;height:32px;border:0;padding:0}p{font-size:12px;color:#687485;margin:0 18px 10px}.annotation-scroll{flex:1;min-height:0;overflow:auto;background:#e9edf3;padding:18px}.annotation-stage{position:relative;width:min(100%,480px);margin:auto;touch-action:none;cursor:crosshair;user-select:none}.annotation-stage img{display:block;width:100%;pointer-events:none}.annotation-stage svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;overflow:hidden}
</style>
