/**
 * 通道管理 API
 */

import { instance, requestApi } from './deviceApi';
import type {
    ChannelCreateRequest,
    ChannelInfo,
    PointImportResult,
    ProtocolConfigResponse
} from '@/types/channel';

/**
 * 获取协议配置选项
 */
export async function getProtocolConfig(): Promise<ProtocolConfigResponse> {
    try {
        const data = await requestApi('/channel/protocols', 'get', null);
        return data;
    } catch (error) {
        console.error('Error fetching protocol config:', error);
        throw error;
    }
}

/**
 * 获取可用串口列表
 */
export async function getSerialPorts(): Promise<Array<{ device: string, description: string }>> {
    try {
        const data = await requestApi('/channel/serial_ports', 'get', null);
        return data;
    } catch (error) {
        console.error('Error fetching serial ports:', error);
        throw error;
    }
}

/**
 * 创建通道
 */
export async function createChannel(channel: ChannelCreateRequest): Promise<{ channel_id: number }> {
    try {
        const data = await requestApi('/channel/create', 'post', channel);
        return data;
    } catch (error) {
        console.error('Error creating channel:', error);
        throw error;
    }
}

/**
 * 导入点表
 */
export async function importPoints(channelId: number, file: File): Promise<PointImportResult> {
    try {
        const formData = new FormData();
        formData.append('channel_id', channelId.toString());
        formData.append('file', file);

        const response = await instance.post('/channel/import_points', formData, {
            headers: {
                'Content-Type': 'multipart/form-data',
            },
        });

        return response.data.data;
    } catch (error) {
        console.error('Error importing points:', error);
        throw error;
    }
}

/**
 * 导入 IEC 61850 ICD 文件
 */
export async function importIcdPoints(channelId: number, file: File): Promise<PointImportResult> {
    try {
        const formData = new FormData();
        formData.append('channel_id', channelId.toString());
        formData.append('file', file);

        const response = await instance.post('/channel/import_icd', formData, {
            headers: {
                'Content-Type': 'multipart/form-data',
            },
        });

        return response.data.data;
    } catch (error) {
        console.error('Error importing ICD file:', error);
        throw error;
    }
}


/**
 * 创建并启动设备
 */
export async function createAndStartDevice(channelId: number): Promise<{ device_name: string }> {
    try {
        const data = await requestApi('/channel/create_and_start', 'post', { channel_id: channelId });
        return data;
    } catch (error) {
        console.error('Error creating and starting device:', error);
        throw error;
    }
}

/**
 * 删除通道
 */
export async function deleteChannel(channelId: number): Promise<boolean> {
    try {
        const response = await instance.delete(`/channel/${channelId}`);
        return response.data.data;
    } catch (error) {
        console.error('Error deleting channel:', error);
        throw error;
    }
}

/**
 * 获取通道列表
 */
export async function getChannelList(): Promise<ChannelInfo[]> {
    try {
        const data = await requestApi('/channel/list', 'get', null);
        return data;
    } catch (error) {
        console.error('Error fetching channel list:', error);
        throw error;
    }
}

/**
 * 获取单个通道详情
 */
export async function getChannel(channelId: number): Promise<ChannelInfo> {
    try {
        const data = await requestApi(`/channel/${channelId}`, 'get', null);
        return data;
    } catch (error) {
        console.error('Error fetching channel:', error);
        throw error;
    }
}

/**
 * 更新通道
 */
export async function updateChannel(channelId: number, channel: Partial<ChannelCreateRequest>): Promise<boolean> {
    try {
        const data = await requestApi(`/channel/${channelId}`, 'put', channel);
        return data;
    } catch (error) {
        console.error('Error updating channel:', error);
        throw error;
    }
}

/**
 * 重启设备（配置更新后）
 */
export async function restartDevice(channelId: number): Promise<{ device_name: string }> {
    try {
        const data = await requestApi(`/channel/restart/${channelId}`, 'post', null);
        return data;
    } catch (error) {
        console.error('Error restarting device:', error);
        throw error;
    }
}

/**
 * 重新加载设备配置（不自动启动服务）
 */
export async function reloadDeviceConfig(channelId: number): Promise<{ device_name: string }> {
    try {
        const data = await requestApi(`/channel/reload_config/${channelId}`, 'post', null);
        return data;
    } catch (error) {
        console.error('Error reloading device config:', error);
        throw error;
    }
}

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

export async function copyDevice(request: CopyDeviceRequest): Promise<CopyDeviceResponse> {
    try {
        const data = await requestApi('/channel/copy', 'post', request);
        return data;
    } catch (error) {
        console.error('Error copying device:', error);
        throw error;
    }
}

export interface IEC61850Structure {
    GOOSE: string[];
    Reports: string[];
    SettingGroups: string[];
    Files: string[];
    DataSets: string[];
    "Data Model": string[];
}

export async function getIEC61850Structure(channelId: number): Promise<IEC61850Structure> {
    try {
        const data = await requestApi(`/channel/iec61850_structure/${channelId}`, 'get', null);
        return data;
    } catch (error) {
        console.error('Error fetching IEC61850 structure:', error);
        throw error;
    }
}

export interface IEC61850TableDataResponse {
    total: number;
    head_data: string[];
    table_data: any[][];
    category: string;
    item: string;
}

export async function iec61850ReadPoints(
    channelId: number,
    category: string = '',
    item: string = '',
    intervalMs: number = 0,
): Promise<{ success: number; fail: number } | null> {
    try {
        const data = await requestApi('/channel/iec61850_read_points/' + channelId, 'post', {
            category,
            item,
            interval_ms: intervalMs,
        });
        return data;
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
        const data = await requestApi(`/channel/iec61850_table_data/${channelId}?${params.toString()}`, 'get', null);
        return new Map<string, any>(Object.entries(data));
    } catch (error) {
        console.error('Error fetching IEC61850 table data:', error);
        throw error;
    }
}
