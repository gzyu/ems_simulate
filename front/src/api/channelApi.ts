/**
 * 通道管理 API
 */

import { instance, requestApi } from './http';
import { CHANNEL_API } from '@/constants';
import type {
  ChannelCreateRequest,
  ChannelInfo,
  PointImportResult,
  ProtocolConfigResponse,
} from '@/types/channel';

// ===== 类型定义 =====

export interface CopyDeviceResult {
  channel_id: number;
  device_id: number;
  name: string;
  code: string;
  ip: string;
}

export interface CopyDeviceResponse {
  copied_count: number;
  devices: CopyDeviceResult[];
}

export interface CopyDeviceRequest {
  channel_id: number;
  count: number;
  prefix?: string;
  suffix?: string;
  ip_start_offset: number;
  port_offset?: number;
}

export interface IEC61850Structure {
  GOOSE: string[];
  Reports: string[];
  SettingGroups: string[];
  Files: string[];
  DataSets: string[];
  "Data Model": string[];
}

export interface IEC61850TableDataResponse {
  total: number;
  head_data: string[];
  table_data: any[][];
  category: string;
  item: string;
}

// ===== API 函数 =====

export async function getProtocolConfig(): Promise<ProtocolConfigResponse> {
  try {
    return await requestApi(CHANNEL_API.PROTOCOLS, 'get', null);
  } catch (error) {
    console.error('Error fetching protocol config:', error);
    throw error;
  }
}

export async function getSerialPorts(): Promise<Array<{ device: string; description: string }>> {
  try {
    return await requestApi(CHANNEL_API.SERIAL_PORTS, 'get', null);
  } catch (error) {
    console.error('Error fetching serial ports:', error);
    throw error;
  }
}

export async function createChannel(channel: ChannelCreateRequest): Promise<{ channel_id: number }> {
  try {
    return await requestApi(CHANNEL_API.CREATE, 'post', channel);
  } catch (error) {
    console.error('Error creating channel:', error);
    throw error;
  }
}

export async function importPoints(channelId: number, file: File): Promise<PointImportResult> {
  try {
    const formData = new FormData();
    formData.append('channel_id', channelId.toString());
    formData.append('file', file);
    const response = await instance.post(CHANNEL_API.IMPORT_POINTS, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data.data;
  } catch (error) {
    console.error('Error importing points:', error);
    throw error;
  }
}

export async function importIcdPoints(channelId: number, file: File): Promise<PointImportResult> {
  try {
    const formData = new FormData();
    formData.append('channel_id', channelId.toString());
    formData.append('file', file);
    const response = await instance.post(CHANNEL_API.IMPORT_ICD, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data.data;
  } catch (error) {
    console.error('Error importing ICD file:', error);
    throw error;
  }
}

export async function createAndStartDevice(channelId: number): Promise<{ device_name: string }> {
  try {
    return await requestApi(CHANNEL_API.CREATE_AND_START, 'post', { channel_id: channelId });
  } catch (error) {
    console.error('Error creating and starting device:', error);
    throw error;
  }
}

export async function deleteChannel(channelId: number): Promise<boolean> {
  try {
    const response = await instance.delete(CHANNEL_API.DELETE(channelId));
    return response.data.data;
  } catch (error) {
    console.error('Error deleting channel:', error);
    throw error;
  }
}

export async function getChannelList(): Promise<ChannelInfo[]> {
  try {
    return await requestApi(CHANNEL_API.LIST, 'get', null);
  } catch (error) {
    console.error('Error fetching channel list:', error);
    throw error;
  }
}

export async function getChannel(channelId: number): Promise<ChannelInfo> {
  try {
    return await requestApi(CHANNEL_API.DETAIL(channelId), 'get', null);
  } catch (error) {
    console.error('Error fetching channel:', error);
    throw error;
  }
}

export async function updateChannel(channelId: number, channel: Partial<ChannelCreateRequest>): Promise<boolean> {
  try {
    return await requestApi(CHANNEL_API.UPDATE(channelId), 'put', channel);
  } catch (error) {
    console.error('Error updating channel:', error);
    throw error;
  }
}

export async function restartDevice(channelId: number): Promise<{ device_name: string }> {
  try {
    return await requestApi(CHANNEL_API.RESTART(channelId), 'post', null);
  } catch (error) {
    console.error('Error restarting device:', error);
    throw error;
  }
}

export async function reloadDeviceConfig(channelId: number): Promise<{ device_name: string }> {
  try {
    return await requestApi(CHANNEL_API.RELOAD_CONFIG(channelId), 'post', null);
  } catch (error) {
    console.error('Error reloading device config:', error);
    throw error;
  }
}

export async function copyDevice(request: CopyDeviceRequest): Promise<CopyDeviceResponse> {
  try {
    return await requestApi(CHANNEL_API.COPY, 'post', request);
  } catch (error) {
    console.error('Error copying device:', error);
    throw error;
  }
}

export async function getIEC61850Structure(channelId: number): Promise<IEC61850Structure> {
  try {
    return await requestApi(CHANNEL_API.IEC61850_STRUCTURE(channelId), 'get', null);
  } catch (error) {
    console.error('Error fetching IEC61850 structure:', error);
    throw error;
  }
}

export async function iec61850ReadPoints(
  channelId: number,
  category: string = '',
  item: string = '',
  intervalMs: number = 0,
): Promise<{ success: number; fail: number } | null> {
  try {
    return await requestApi(CHANNEL_API.IEC61850_READ_POINTS(channelId), 'post', {
      category,
      item,
      interval_ms: intervalMs,
    });
  } catch (error) {
    console.error('Error reading IEC61850 points:', error);
    throw error;
  }
}

export async function getIEC61850TableData(
  channelId: number,
  category: string = '',
  item: string = '',
  pointName: string | null = null,
  pageIndex: number = 1,
  pageSize: number = 10,
  pointTypes: number[] = [],
): Promise<Map<string, any>> {
  try {
    const params = new URLSearchParams();
    if (category) params.append('category', category);
    if (item) params.append('item', item);
    if (pointName) params.append('point_name', pointName);
    params.append('page_index', pageIndex.toString());
    params.append('page_size', pageSize.toString());
    if (pointTypes.length > 0) {
      params.append('point_types', pointTypes.join(','));
    }
    const data = await requestApi(`${CHANNEL_API.IEC61850_TABLE_DATA(channelId)}?${params.toString()}`, 'get', null);
    return new Map<string, any>(Object.entries(data));
  } catch (error) {
    console.error('Error fetching IEC61850 table data:', error);
    throw error;
  }
}
