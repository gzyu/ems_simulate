/**
 * 侧边栏树刷新触发器
 * 模块级共享状态，用于跨组件通知侧边栏刷新
 */
import { ref } from 'vue';

// 模块级共享变量，所有导入此模块的组件共享同一个实例
const refreshCounter = ref(0);
const refreshDeviceName = ref<string>('');

/**
 * 触发侧边栏树结构刷新
 * @param deviceName 可选，指定需要刷新的设备名
 */
export function triggerSidebarRefresh(deviceName?: string) {
  refreshCounter.value++;
  if (deviceName) refreshDeviceName.value = deviceName;
}

/**
 * 监听侧边栏刷新触发
 */
export function useSidebarRefresh() {
  return { refreshCounter, refreshDeviceName };
}
