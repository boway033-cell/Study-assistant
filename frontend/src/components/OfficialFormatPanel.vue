<template>
  <el-collapse v-if="profile" class="official-format">
    <el-collapse-item title="Word 排版" name="format">
      <div class="format-basics">
        <label v-for="(label, side) in margins" :key="side">{{ label }}边距 / cm
          <el-input-number v-model="profile.page.margins_cm[side]" :min="0.5" :max="10" :step="0.1" :precision="1" :aria-label="`${label}边距`" />
        </label>
        <label>打印方式<el-select v-model="profile.page.print_mode" aria-label="打印方式"><el-option label="双面" value="duplex" /><el-option label="单面" value="single" /></el-select></label>
        <el-checkbox v-model="profile.global.bold">全文加粗</el-checkbox>
      </div>
      <el-collapse>
        <el-collapse-item v-for="(label, role) in roles" :key="role" :title="label" :name="role">
          <div class="style-fields">
            <label>中文字体<el-input v-model="profile.styles[role].font_cn" :maxlength="100" :aria-label="`${label}中文字体`" /></label>
            <label>备用字体<el-input v-model="profile.styles[role].font_fallback" :maxlength="100" :aria-label="`${label}备用字体`" /></label>
            <label>西文字体<el-input v-model="profile.styles[role].font_latin" :maxlength="100" :aria-label="`${label}西文字体`" /></label>
            <label>字号 / pt<el-input-number v-model="profile.styles[role].size_pt" :min="5" :max="72" :aria-label="`${label}字号`" /></label>
            <label>行距 / pt<el-input-number v-model="profile.styles[role].line_spacing_pt" :min="5" :max="100" :aria-label="`${label}行距`" /></label>
            <label>段前 / pt<el-input-number v-model="profile.styles[role].space_before_pt" :min="0" :max="100" :aria-label="`${label}段前`" /></label>
            <label>首行 / 字<el-input-number v-model="profile.styles[role].first_line_chars" :min="0" :max="20" :aria-label="`${label}首行缩进`" /></label>
            <label>对齐<el-select v-model="profile.styles[role].alignment" :aria-label="`${label}对齐`"><el-option v-for="(name, key) in alignments" :key="key" :label="name" :value="key" /></el-select></label>
          </div>
        </el-collapse-item>
        <el-collapse-item title="页码" name="page-number">
          <div class="style-fields">
            <label>字体<el-input v-model="profile.page_number.font_cn" :maxlength="100" aria-label="页码字体" /></label>
            <label>备用字体<el-input v-model="profile.page_number.font_fallback" :maxlength="100" aria-label="页码备用字体" /></label>
            <label>字号 / pt<el-input-number v-model="profile.page_number.size_pt" :min="5" :max="72" aria-label="页码字号" /></label>
            <el-checkbox v-model="profile.page_number.bold">页码加粗</el-checkbox>
          </div>
        </el-collapse-item>
      </el-collapse>
      <div class="format-actions"><el-button :disabled="busy" @click="resetPreset">恢复预设</el-button><el-button :disabled="busy || !changes.length" @click="confirmOpen = true">保存为默认…</el-button></div>
      <el-dialog v-model="confirmOpen" title="确认默认排版变更" width="min(620px, 92vw)" append-to-body>
        <div class="change-list"><div v-for="change in changes" :key="change.field"><code>{{ change.field }}</code><span>{{ change.before }} → {{ change.after }}</span></div></div>
        <template #footer><el-button @click="confirmOpen = false">取消</el-button><el-button type="primary" :loading="busy" @click="save">确认保存</el-button></template>
      </el-dialog>
    </el-collapse-item>
  </el-collapse>
</template>

<script setup>
import { computed, ref } from 'vue'
import { formatChanges } from '../utils/officialFormat'
const props = defineProps({ record: Object, busy: Boolean })
const profile = defineModel({ type: Object })
const emit = defineEmits(['save'])
const confirmOpen = ref(false)
const margins = { top: '上', bottom: '下', left: '左', right: '右' }
const roles = { main_title: '标题', heading1: '一级标题', heading2: '二级标题', body: '正文', reference_note: '引用', description: '说明' }
const alignments = { left: '左对齐', center: '居中', right: '右对齐', justify: '两端对齐' }
const changes = computed(() => formatChanges(profile.value, props.record?.profile))
const resetPreset = () => { profile.value = JSON.parse(JSON.stringify(props.record.preset)) }
const save = () => { confirmOpen.value = false; emit('save') }
</script>

<style scoped>
.official-format{margin-top:18px}.format-basics,.style-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;padding:10px 0}.format-basics label,.style-fields label{display:grid;gap:6px;font-size:12px;color:var(--study-text-secondary)}.el-input-number{width:100%;max-width:100%}.format-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.format-actions .el-button{margin:0}.change-list{max-height:55vh;overflow:auto}.change-list>div{display:grid;gap:4px;padding:8px 0;border-bottom:1px solid var(--study-card-border);overflow-wrap:anywhere}
</style>
