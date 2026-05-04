<template>
  <div class="goose-view">
    <GooseManager />
  </div>
</template>

<script setup lang="ts">
import GooseManager from '@/components/goose/GooseManager.vue'
</script>

<style scoped>
.goose-view {
  height: 100%;
  overflow: auto;
}
</style>
