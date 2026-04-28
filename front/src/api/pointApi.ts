import { PointType, type PointLimit } from '@/types/point';
import axios from 'axios';
import { ElMessage } from 'element-plus';
import { requestApi } from '@/api/deviceApi';

export interface PointCreateData {
    frame_type: number;  // 0=遥测, 1=遥信, 2=遥控, 3=遥调
    code: string;
    name: string;
    rtu_addr: number;
    reg_addr: string;
    func_code: number;
    decode_code: string;
    bit?: number | null;
    mul_coe: number;
    add_coe: number;
    iec_type_id?: string | null;
}

export interface ChangeRecord {
    source: string;
    source_label: string;
    old_value: any;
    new_value: any;
    old_real_value: any;
    new_real_value: any;
    timestamp: number;
    time: string;
    detail: string;
    client_info?: string;
}

export interface PointChangeHistoryResponse {
    point_code: string;
    tracking_enabled: boolean;
    maxlen: number;
    history: ChangeRecord[];
    count: number;
}


export async function editPointData(deviceName: string, pointCode: string, pointValue: number): Promise<boolean> {
    const data = await requestApi('/point/edit_point_data/', 'post', {
        device_name: deviceName,
        point_code: pointCode,
        point_value: pointValue,
    });
    return data;
}


export async function editPointLimit(deviceName: string, pointCode: string, minValueLimit: number, maxValueLimit: number): Promise<boolean> {
    try {
        const data = await requestApi('/point/edit_point_limit/', 'post', {
            device_name: deviceName,
            point_code: pointCode,
            min_value_limit: minValueLimit,
            max_value_limit: maxValueLimit,
        });
        return data;
    } catch (error) {
        console.error('Error editing point limit:', error);
        throw error;
    }
}

export async function getPointLimit(deviceName: string, pointCode: string): Promise<PointLimit> {
    const pointLimit = {
        minValueLimit: 0,
        maxValueLimit: 0,
    };

    try {
        const data = await requestApi('/point/get_point_limit/', 'post', {
            device_name: deviceName,
            point_code: pointCode,
        });
        pointLimit.minValueLimit = data.min_value_limit;
        pointLimit.maxValueLimit = data.max_value_limit;
        return pointLimit;
    } catch (error) {
        console.error('Error getting point limit:', error);
        return pointLimit;
    }
}


export async function getPointInfo(deviceName: string, pointCode: string): Promise<any> {
    try {
        const data = await requestApi('/point/get_point_info', 'post', {
            device_name: deviceName,
            point_code: pointCode,
        });
        return data;
    } catch (error) {
        console.error('Error getting point info:', error);
        throw error;
    }
}

export async function setSinglePointSimulateMethod(deviceName: string, pointCode: string, simulateMethod: string): Promise<boolean> {
    try {
        const data = await requestApi('/point/set_single_point_simulate_method', 'post', {
            device_name: deviceName,
            point_code: pointCode,
            simulate_method: simulateMethod,
        });
        return data;
    } catch (error) {
        console.error('Error setting single point simulate method:', error);
        throw error;
    }
}

export async function setSinglePointStep(deviceName: string, pointCode: string, step: number): Promise<boolean> {
    try {
        const data = await requestApi('/point/set_single_point_step', 'post', {
            device_name: deviceName,
            point_code: pointCode,
            step: step,
        });
        return data;
    } catch (error) {
        console.error('Error setting single point step:', error);
        throw error;
    }
}

export async function setPointSimulationRange(deviceName: string, pointCode: string, minValue: number, maxValue: number): Promise<boolean> {
    try {
        const data = await requestApi('/point/set_point_simulation_range', 'post', {
            device_name: deviceName,
            point_code: pointCode,
            min_value: minValue,
            max_value: maxValue,
        });
        return data;
    } catch (error) {
        console.error('Error setting point simulation range:', error);
        throw error;
    }
}

export async function editPointMetadata(deviceName: string, pointCode: string, metadata: any): Promise<boolean> {
    try {
        const data = await requestApi('/point/edit_point_metadata/', 'post', {
            device_name: deviceName,
            point_code: pointCode,
            metadata: metadata,
        });
        return data;
    } catch (error) {
        console.error('Error editing point metadata:', error);
        throw error;
    }
}


export async function readSinglePoint(deviceName: string, pointCode: string): Promise<number | null> {
    try {
        const data = await requestApi('/point/read_single_point', 'post', {
            device_name: deviceName,
            point_code: pointCode,
        });
        return data?.value ?? null;
    } catch (error) {
        console.error('Error reading single point:', error);
        return null;
    }
}


export async function addPoint(deviceName: string, pointData: PointCreateData): Promise<boolean> {
    try {
        const data = await requestApi('/point/add_point', 'post', {
            device_name: deviceName,
            ...pointData,
        });
        return data;
    } catch (error) {
        console.error('Error adding point:', error);
        throw error;
    }
}

export async function addPointsBatch(deviceName: string, frameType: number, points: PointCreateData[]): Promise<boolean> {
    try {
        const data = await requestApi('/point/add_points_batch', 'post', {
            device_name: deviceName,
            frame_type: frameType,
            points: points,
        });
        return data;
    } catch (error) {
        console.error('Error adding points batch:', error);
        throw error;
    }
}

export async function deletePoint(deviceName: string, pointCode: string): Promise<boolean> {
    try {
        const data = await requestApi('/point/delete_point', 'post', {
            device_name: deviceName,
            point_code: pointCode,
        });
        return data;
    } catch (error) {
        console.error('Error deleting point:', error);
        throw error;
    }
}

// ===== 变更追溯 =====

export async function getPointChangeHistory(deviceName: string, pointCode: string): Promise<PointChangeHistoryResponse | null> {
    try {
        const data = await requestApi('/point/get_point_change_history', 'post', {
            device_name: deviceName,
            point_code: pointCode,
        });
        return data;
    } catch (error) {
        console.error('Error getting point change history:', error);
        return null;
    }
}

export async function setChangeTrackingConfig(deviceName: string, pointCode: string, enabled: boolean, maxlen?: number): Promise<boolean> {
    try {
        const data = await requestApi('/point/set_change_tracking', 'post', {
            device_name: deviceName,
            point_code: pointCode,
            enabled: enabled,
            maxlen: maxlen
        });
        return data;
    } catch (error) {
        console.error('Error setting change tracking config:', error);
        throw error;
    }
}

export async function clearPointChangeHistory(deviceName: string, pointCode: string): Promise<boolean> {
    try {
        const data = await requestApi('/point/clear_point_change_history', 'post', {
            device_name: deviceName,
            point_code: pointCode,
        });
        return data;
    } catch (error) {
        console.error('Error clearing point change history:', error);
        return false;
    }
}

export async function clearPoints(deviceName: string, slaveId: number): Promise<number> {
    try {
        const data = await requestApi('/point/clear_points', 'post', {
            device_name: deviceName,
            slave_id: slaveId,
        });
        return data;
    } catch (error) {
        console.error('Error clearing points:', error);
        throw error;
    }
}

export async function resetPointData(deviceName: string): Promise<boolean> {
    try {
        const data = await requestApi('/point/reset_point_data', 'post', {
            device_name: deviceName,
        });
        return data;
    } catch (error) {
        console.error('Error resetting point data:', error);
        throw error;
    }
}
