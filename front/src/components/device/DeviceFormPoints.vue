<template>
  <div class="device-form-points">
    <!-- IEC 61850 协议: ICD 文件导入 -->
    <template v-if="protocolType === 4">
      <el-divider content-position="left">ICD 文件导入</el-divider>
      
      <el-form-item label="ICD文件">
        <el-upload
          ref="icdUploadRef"
          action="#"
          :auto-upload="true"
          :limit="1"
          :http-request="handleIcdFileRequest"
          accept=".icd,.scd,.cid,.xml"
        >
          <template #trigger>
            <el-button type="success" plain :icon="Upload">选择 ICD 文件</el-button>
          </template>
          <template #tip>
            <div class="el-upload__tip">
              支持 .icd / .scd / .cid / .xml 格式（IEC 61850 SCL 文件）
            </div>
          </template>
        </el-upload>
      </el-form-item>
    </template>
    
    <!-- 其他协议: Excel 点表导入 -->
    <template v-else>
      <el-divider content-position="left">点表导入</el-divider>
      
      <el-form-item label="测点表格">
        <el-upload
          ref="uploadRef"
          action="#"
          :auto-upload="true"
          :limit="1"
          :http-request="handleFileRequest"
          accept=".xlsx,.xls"
        >
          <template #trigger>
            <el-button type="success" plain :icon="Upload">选择 Excel 文件</el-button>
          </template>
          <template #tip>
            <div class="el-upload__tip">
              支持 .xlsx 格式，包含遥测/遥信/遥控/遥调 四个 sheet
            </div>
          </template>
        </el-upload>
      </el-form-item>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { ref } from 'vue';
import { Upload } from "@element-plus/icons-vue";

const props = defineProps<{
  protocolType?: number;
}>();

const uploadRef = ref();
const icdUploadRef = ref();

const emit = defineEmits<{
  (e: 'file-change', file: any): void;
  (e: 'icd-file-change', file: any): void;
}>();

const handleFileRequest = (options: any) => {
  emit('file-change', options.file);
  return Promise.resolve();
};

const handleIcdFileRequest = (options: any) => {
  emit('icd-file-change', options.file);
  return Promise.resolve();
};

const clearFiles = () => {
  uploadRef.value?.clearFiles();
  icdUploadRef.value?.clearFiles();
};

defineExpose({ clearFiles });
</script>

<style lang="scss" scoped>
.device-form-points {
  margin-top: 10px;
}
</style>
