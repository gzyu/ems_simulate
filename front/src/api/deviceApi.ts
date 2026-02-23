import { PointType, type PointLimit } from '@/types/point';
import axios from 'axios';
import { ElMessage } from 'element-plus';

// const apiUrl = "http://127.0.0.1:8888";
// 使用相对路径，Nginx会代理到正确的后端地址
const API_BASE_URL = import.meta.env.VUE_APP_API_BASE || '/'; // Nginx会将/api代理到实际后端

export const instance = axios.create({
    baseURL: API_BASE_URL,
    timeout: 3000,
    headers: {
        'Content-Type': 'application/json'
    }
});

// 添加响应拦截器
instance.interceptors.response.use(
    (response) => {
        // 检查业务响应码
        if (response.data && response.data.code !== 200) {
            const errorMsg = response.data.message || '请求失败';
            ElMessage.error(errorMsg);
            return Promise.reject(new Error(errorMsg));
        }
        return response;
    },
    (error) => {
        // 统一错误处理
        let message = '网络请求失败';
        if (axios.isAxiosError(error)) {
            message = error.response?.data?.message || error.message;
        } else if (error instanceof Error) {
            message = error.message;
        }
        ElMessage.error(message);
        return Promise.reject(error);
    }
);

// 封装请求方法
export const requestApi = async (url: string, method: string, data: any): Promise<any> => {
    const response = await instance.request({
        url,
        method,
        data
    });
    return response.data.data;
}

export async function getDeviceList(): Promise<Array<string>> {
    try {
        const data = await requestApi(`/device/get_device_list`, 'post', null);
        return data;
    } catch (error) {
        console.error('Error fetching device list:', error);
        throw error;
    }
}

export async function getDeviceInfo(deviceName: string): Promise<Map<string, any>> {
    try {
        const data = await requestApi(`/device/get_device_info`, 'post', { device_name: deviceName });
        // 将返回的对象转换为 Map
        const deviceInfoMap = new Map<string, any>(Object.entries(data));
        return deviceInfoMap;
    } catch (error) {
        console.error('Error fetching device info:', error);
        throw error;
    }
}

export async function startSimulation(deviceName: string, simulateMethod: string): Promise<boolean> {
    try {
        const data = await requestApi(`/device/start_simulation`, 'post',
            {
                device_name: deviceName,
                simulate_method: simulateMethod
            },
        );
        return data;
    } catch (error) {
        console.error('Error start simulation:', error);
        throw error;
    }
}


export async function stopSimulation(deviceName: string): Promise<boolean> {
    try {
        const data = await requestApi(`/device/stop_simulation`, 'post', {
            device_name: deviceName,
        },
        );
        return data;
    } catch (error) {
        console.error('Error stop simulation:', error);
        throw error;
    }
}

export async function startDevice(deviceName: string): Promise<boolean> {
    try {
        const data = await requestApi(`/device/start`, 'post', {
            device_name: deviceName,
        });
        return data;
    } catch (error) {
        console.error('Error starting device:', error);
        throw error;
    }
}

export async function stopDevice(deviceName: string): Promise<boolean> {
    try {
        const data = await requestApi(`/device/stop`, 'post', {
            device_name: deviceName,
        });
        return data;
    } catch (error) {
        console.error('Error stopping device:', error);
        throw error;
    }
}

export async function getSlaveIdList(deviceName: string): Promise<Array<number>> {
    try {
        const data = await requestApi(`/device/get_slave_id_list`, 'post',
            {
                device_name: deviceName,
            },
        );
        return data;
    } catch (error) {
        console.error('Error stop simulation:', error);
        throw error;
    }
}

export async function getDeviceTable(deviceName: string, slaveId: number, pointName: string | null, pageIndex: number,
    pageSize: number, pointTypes: number[], orderBy: string | null = null, orderDirection: string | null = null): Promise<Map<string, any>> {
    try {
        const data = await requestApi(`/device/get_device_table`, 'post', {
            device_name: deviceName,
            slave_id: slaveId,
            point_name: pointName,
            page_index: pageIndex,
            page_size: pageSize,
            point_types: pointTypes,
            order_by: orderBy,
            order_direction: orderDirection
        });
        // 将返回的对象转换为 Map
        const deviceInfoMap = new Map<string, any>(Object.entries(data));
        return deviceInfoMap;
    } catch (error) {
        console.error('Error stop simulation:', error);
        throw error;
    }
}



// ===== 自动读取控制 =====

export async function getAutoReadStatus(deviceName: string): Promise<boolean> {
    try {
        const data = await requestApi('/device/get_auto_read_status', 'post', {
            device_name: deviceName,
        });
        return data;
    } catch (error) {
        console.error('Error getting auto read status:', error);
        return false;
    }
}

export async function startAutoRead(deviceName: string): Promise<boolean> {
    try {
        const data = await requestApi('/device/start_auto_read', 'post', {
            device_name: deviceName,
        });
        return data;
    } catch (error) {
        console.error('Error starting auto read:', error);
        throw error;
    }
}

export async function stopAutoRead(deviceName: string): Promise<boolean> {
    try {
        const data = await requestApi('/device/stop_auto_read', 'post', {
            device_name: deviceName,
        });
        return data;
    } catch (error) {
        console.error('Error stopping auto read:', error);
        throw error;
    }
}

export async function manualRead(deviceName: string, interval: number = 0): Promise<any> {
    try {
        const data = await requestApi('/device/manual_read', 'post', {
            device_name: deviceName,
            interval: interval
        });
        return data;
    } catch (error) {
        console.error('Error performing manual read:', error);
        throw error;
    }
}


// ===== 报文捕获 =====

export interface MessageRecord {
    timestamp: number;
    formatted_time: string;
    direction: string;
    hex_data: string;
    raw_hex: string;
    description: string;
    length: number;
}

export async function getMessages(deviceName: string, limit: number = 100): Promise<MessageRecord[]> {
    try {
        const data = await requestApi('/device/get_messages', 'post', {
            device_name: deviceName,
            limit: limit,
        });
        return data?.messages ?? [];
    } catch (error) {
        console.error('Error getting messages:', error);
        return [];
    }
}

export async function clearMessages(deviceName: string): Promise<boolean> {
    try {
        const data = await requestApi('/device/clear_messages', 'post', {
            device_name: deviceName,
        });
        return data;
    } catch (error) {
        console.error('Error clearing messages:', error);
        throw error;
    }
}

export interface AvgTimeStats {
    tx_count: number;
    rx_count: number;
    total_count: number;
    pair_count: number;
    avg_latency_ms: number;
}

export async function getAvgTime(deviceName: string): Promise<AvgTimeStats | null> {
    try {
        const data = await requestApi('/device/get_avg_time', 'post', {
            device_name: deviceName,
        });
        return data;
    } catch (error) {
        console.error('Error getting avg time:', error);
        return null;
    }
}

// ===== 动态测点/从机管理 =====

export async function addSlave(deviceName: string, slaveId: number): Promise<boolean> {
    try {
        const data = await requestApi('/device/add_slave', 'post', {
            device_name: deviceName,
            slave_id: slaveId,
        });
        return data;
    } catch (error) {
        console.error('Error adding slave:', error);
        throw error;
    }
}

export async function deleteSlave(deviceName: string, slaveId: number): Promise<boolean> {
    try {
        const data = await requestApi('/device/delete_slave', 'post', {
            device_name: deviceName,
            slave_id: slaveId,
        });
        return data;
    } catch (error) {
        console.error('Error deleting slave:', error);
        throw error;
    }
}

export async function editSlave(deviceName: string, oldSlaveId: number, newSlaveId: number): Promise<boolean> {
    try {
        const data = await requestApi('/device/edit_slave', 'post', {
            device_name: deviceName,
            old_slave_id: oldSlaveId,
            new_slave_id: newSlaveId,
        });
        return data;
    } catch (error) {
        console.error('Error editing slave:', error);
        throw error;
    }
}


