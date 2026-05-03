/**
 * IEC61850 设备树结构 composable
 * 从 SideBar.vue 中提取的 IEC61850 树节点构建与标记逻辑
 */

import { ref } from 'vue';
import { getChannelList } from '@/api/channelApi';
import { getIEC61850Structure } from '@/api/channelApi';
import { IEC61850_CATEGORIES } from '@/constants/protocol';
import type { DeviceInfo } from '@/api/deviceGroupApi';

export interface TreeNode {
  nodeKey: string;
  label: string;
  isGroup: boolean;
  id: number;
  name: string;
  groupId?: number;
  children?: TreeNode[];
  isIec61850?: boolean;
  isIec61850Child?: boolean;
  iec61850ChannelId?: number;
  iec61850Children?: TreeNode[];
  deviceName?: string;
  type?: string;
  value?: string;
  iec61850Level?: 'category' | 'ld' | 'ln';
}

/**
 * 根据 IEC61850 结构数据构建树节点
 */
export function buildIEC61850Children(structure: any, deviceName: string, keyPrefix: string): TreeNode[] {
  const children: TreeNode[] = [];
  IEC61850_CATEGORIES.forEach((cat) => {
    const items = structure[cat.key] || [];
    if (items.length > 0) {
      let categoryChildren: TreeNode[];

      if (cat.key === 'Data Model') {
        // Data Model 返回层级结构: [{name: "LD0", children: ["LLN0", "MMXU1"]}, ...]
        categoryChildren = items.map((ldItem: any, ldIndex: number) => {
          const ldName = typeof ldItem === 'string' ? ldItem : ldItem.name;
          const lnList = typeof ldItem === 'object' && ldItem.children ? ldItem.children : [];
          const lnChildren: TreeNode[] = lnList.map((ln: string, lnIndex: number) => ({
            nodeKey: `${keyPrefix}-${deviceName}-${cat.key}-${ldIndex}-${lnIndex}`,
            label: ln,
            isGroup: false,
            id: 0,
            isIec61850Child: true,
            iec61850Level: 'ln' as const,
            name: ln,
            deviceName: deviceName,
            type: cat.label,
            value: `${ldName}/${ln}`,
          }));
          return {
            nodeKey: `${keyPrefix}-${deviceName}-${cat.key}-${ldIndex}`,
            label: ldName,
            isGroup: lnChildren.length > 0,
            id: 0,
            isIec61850Child: true,
            iec61850Level: 'ld' as const,
            name: ldName,
            deviceName: deviceName,
            type: cat.label,
            value: ldName,
            children: lnChildren.length > 0 ? lnChildren : undefined,
          };
        });
      } else {
        // 其他分类: 仍然为扁平列表
        categoryChildren = items.map((item: string, itemIndex: number) => ({
          nodeKey: `${keyPrefix}-${deviceName}-${cat.key}-${itemIndex}`,
          label: item,
          isGroup: false,
          id: 0,
          isIec61850Child: true,
          iec61850Level: 'ld' as const,
          name: item,
          deviceName: deviceName,
          type: cat.label,
        }));
      }

      children.push({
        nodeKey: `${keyPrefix}-${deviceName}-${cat.key}`,
        label: cat.label,
        isGroup: true,
        id: 0,
        isIec61850Child: true,
        iec61850Level: 'category' as const,
        name: cat.label,
        deviceName: deviceName,
        type: cat.label,
        children: categoryChildren,
      });
    } else {
      children.push({
        nodeKey: `${keyPrefix}-${deviceName}-${cat.key}`,
        label: cat.label,
        isGroup: false,
        id: 0,
        isIec61850Child: true,
        iec61850Level: 'category' as const,
        name: cat.label,
        deviceName: deviceName,
        type: cat.label,
      });
    }
  });
  return children;
}

/**
 * 构建空的 IEC61850 回退节点（获取结构失败时使用）
 */
export function buildFallbackIEC61850Children(deviceName: string, keyPrefix: string): TreeNode[] {
  return IEC61850_CATEGORIES.map(cat => ({
    nodeKey: `${keyPrefix}-${deviceName}-${cat.key}`,
    label: cat.label,
    isGroup: false,
    id: 0,
    isIec61850Child: true,
    name: cat.label,
    deviceName: deviceName,
    type: cat.label,
  }));
}

/**
 * IEC61850 树节点管理 composable
 */
export function useIec61850Tree() {
  const iec61850UngroupedMap = ref<Record<string, TreeNode[]>>({});

  /**
   * 获取分组内设备的 IEC61850 结构并更新树
   */
  const fetchIEC61850Structure = async (channelId: number, deviceName: string, treeData: Ref<TreeNode[]>) => {
    const updateTreeNode = (nodes: TreeNode[], iec61850Children: TreeNode[]) => {
      for (const node of nodes) {
        if (node.name === deviceName && node.isIec61850) {
          node.children = iec61850Children;
          return true;
        }
        if (node.children && updateTreeNode(node.children, iec61850Children)) {
          return true;
        }
      }
      return false;
    };

    try {
      const structure = await getIEC61850Structure(channelId);
      const iec61850Children = buildIEC61850Children(structure, deviceName, 'device');
      updateTreeNode(treeData.value, iec61850Children);
      treeData.value = [...treeData.value];
    } catch (error) {
      console.warn(`获取 IEC61850 结构失败 (设备: ${deviceName}), 显示默认结构:`, error);
      const fallback = buildFallbackIEC61850Children(deviceName, 'device');
      updateTreeNode(treeData.value, fallback);
      treeData.value = [...treeData.value];
    }
  };

  /**
   * 标记分组内的 IEC61850 设备并异步获取结构
   */
  const markIEC61850Devices = async (nodes: TreeNode[], treeData: Ref<TreeNode[]>) => {
    try {
      const channels = await getChannelList();
      for (const node of nodes) {
        if (!node.isGroup && node.name) {
          const channel = channels.find(c => c.name === node.name);
          if (channel && channel.protocol_type === 4) {
            node.isIec61850 = true;
            node.iec61850ChannelId = channel.id;
            fetchIEC61850Structure(channel.id, node.name, treeData);
          }
        }
        if (node.children) {
          await markIEC61850Devices(node.children, treeData);
        }
      }
    } catch (error) {
      console.error('标记 IEC61850 设备失败:', error);
    }
  };

  /**
   * 标记并获取未分组设备的 IEC61850 结构
   */
  const markUngroupedIEC61850Devices = async (devices: DeviceInfo[]) => {
    try {
      const channels = await getChannelList();
      for (const device of devices) {
        const channel = channels.find(c => c.name === device.name);
        if (channel && channel.protocol_type === 4) {
          (async () => {
            try {
              const structure = await getIEC61850Structure(channel.id);
              iec61850UngroupedMap.value = {
                ...iec61850UngroupedMap.value,
                [device.name]: buildIEC61850Children(structure, device.name, 'ungrouped'),
              };
            } catch (error) {
              console.warn(`获取未分组 IEC61850 结构失败 (设备: ${device.name}):`, error);
              iec61850UngroupedMap.value = {
                ...iec61850UngroupedMap.value,
                [device.name]: buildFallbackIEC61850Children(device.name, 'ungrouped'),
              };
            }
          })();
        }
      }
    } catch (error) {
      console.error('标记未分组 IEC61850 设备失败:', error);
    }
  };

  return {
    iec61850UngroupedMap,
    fetchIEC61850Structure,
    markIEC61850Devices,
    markUngroupedIEC61850Devices,
  };
}

import type { Ref } from 'vue';
