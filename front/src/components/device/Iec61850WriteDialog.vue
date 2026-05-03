<template>
  <el-dialog
    v-model="visible"
    title="写入值"
    width="400px"
    destroy-on-close
    :close-on-click-modal="false"
  >
    <el-form :model="form" label-width="80px">
      <el-form-item label="测点编码">
        <el-input v-model="form.pointCode" disabled />
      </el-form-item>
      <el-form-item label="真实值">
        <el-input :model-value="String(form.currentValue)" disabled />
      </el-form-item>
      <!-- 遥控 (YK): 合/分 -->
      <el-form-item v-if="pointType === 2" label="操作">
        <el-radio-group v-model="form.writeValue">
          <el-radio :label="1">合 / 开 (1)</el-radio>
          <el-radio :label="0">分 / 关 (0)</el-radio>
        </el-radio-group>
      </el-form-item>
      <!-- 其他类型: 自由输入 (数值/字符串) -->
      <el-form-item v-else label="写入值">
        <el-input
          v-model="form.writeValue"
          placeholder="输入数值或字符串"
          style="width: 100%"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <span class="dialog-footer">
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="loading" @click="handleSubmit">
          确认写入
        </el-button>
      </span>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { iec61850WritePoint } from '@/api/channelApi';

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  channelId: { type: Number, required: true },
  pointCode: { type: String, default: '' },
  currentValue: { type: [Number, String], default: '' },
  pointType: { type: Number, default: 0 },
});

const emit = defineEmits(['update:modelValue', 'success']);

const visible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
});

const loading = ref(false);
const form = reactive({
  pointCode: '',
  currentValue: '' as string | number,
  writeValue: '' as string | number,
});

watch(() => props.modelValue, (val) => {
  if (val) {
    form.pointCode = props.pointCode;
    form.currentValue = props.currentValue;
    form.writeValue = props.pointType === 2 ? 1 : String(props.currentValue ?? '');
  }
}, { immediate: true });

const handleSubmit = async () => {
  if (!form.pointCode) {
    ElMessage.warning('测点编码为空，无法写入');
    return;
  }
  loading.value = true;
  try {
    // 智能判断值类型: 纯数字转为数字, 否则保留字符串
    let val: string | number = form.writeValue;
    if (typeof val === 'string' && val.trim() !== '' && !isNaN(Number(val))) {
      val = Number(val);
    }
    const result = await iec61850WritePoint(props.channelId, form.pointCode, val);
    if (result) {
      ElMessage.success('写入指令已发送');
      visible.value = false;
      emit('success');
    }
  } catch (e: any) {
    ElMessage.error(`写入失败: ${e?.message || e}`);
  } finally {
    loading.value = false;
  }
};
</script>

<style scoped>
::deep(.el-input__inner) {
  text-align: center;
}
</style>
