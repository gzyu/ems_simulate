/**
 * 测点映射 API
 */

import { requestApi } from './http';
import { POINT_MAPPING_API } from '@/constants';

export interface PointMapping {
  id: number;
  device_name: string;
  target_point_code: string;
  source_point_codes: string;
  formula: string;
  enable: boolean;
}

export interface PointMappingCreate {
  device_name: string;
  target_point_code: string;
  source_point_codes: any[];
  formula: string;
  enable: boolean;
}

export interface PointMappingUpdate {
  device_name?: string;
  target_point_code?: string;
  source_point_codes?: any[];
  formula?: string;
  enable?: boolean;
}

export async function getMappings(): Promise<PointMapping[]> {
  try {
    return await requestApi(POINT_MAPPING_API.LIST, 'post', null);
  } catch (error) {
    console.error('Error fetching mappings:', error);
    throw error;
  }
}

export async function createMapping(data: PointMappingCreate): Promise<PointMapping> {
  try {
    return await requestApi(POINT_MAPPING_API.CREATE, 'post', data);
  } catch (error) {
    console.error('Error creating mapping:', error);
    throw error;
  }
}

export async function updateMapping(id: number, data: PointMappingUpdate): Promise<boolean> {
  try {
    return await requestApi(POINT_MAPPING_API.UPDATE, 'post', { id, ...data });
  } catch (error) {
    console.error('Error updating mapping:', error);
    throw error;
  }
}

export async function deleteMapping(id: number): Promise<boolean> {
  try {
    return await requestApi(POINT_MAPPING_API.DELETE, 'post', { mapping_id: id });
  } catch (error) {
    console.error('Error deleting mapping:', error);
    throw error;
  }
}
