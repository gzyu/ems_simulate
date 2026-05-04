/**
 * 测点树 API
 */

import { requestApi } from './http';
import { POINT_TREE_API } from '@/constants';

export interface PointLeaf {
  code: string;
  name: string;
  value: any;
  rtu_addr: number;
  reg_addr: string;
  type: string;
}

export interface TypeNode {
  label: string;
  children: PointLeaf[];
}

export interface DeviceNode {
  label: string;
  children: TypeNode[];
}

export interface TreeResponse {
  code: number;
  message: string;
  data: DeviceNode[];
}

export async function getPointTree(): Promise<DeviceNode[]> {
  try {
    return await requestApi(POINT_TREE_API.TREE, 'post', null);
  } catch (error) {
    console.error('Error fetching point tree:', error);
    throw error;
  }
}
