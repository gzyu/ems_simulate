/**
 * API 路径常量
 * 集中管理所有后端 API 路径，避免在各处硬编码
 */

// ===== 通道相关 =====
export const CHANNEL_API = {
  PROTOCOLS: '/api/channels/protocols',
  SERIAL_PORTS: '/api/channels/serial-ports',
  CREATE: '/api/channels/create',
  IMPORT_POINTS: '/api/channels/import-points',
  IMPORT_ICD: '/api/channels/import-icd',
  CREATE_AND_START: '/api/channels/create-and-start',
  DELETE: (id: number) => `/api/channels/${id}`,
  LIST: '/api/channels/list',
  DETAIL: (id: number) => `/api/channels/${id}`,
  UPDATE: (id: number) => `/api/channels/${id}`,
  RESTART: (id: number) => `/api/channels/restart/${id}`,
  RELOAD_CONFIG: (id: number) => `/api/channels/reload-config/${id}`,
  COPY: '/api/channels/copy',
  IEC61850_STRUCTURE: (id: number) => `/api/channels/iec61850-structure/${id}`,
  IEC61850_READ_POINTS: (id: number) => `/api/channels/iec61850-read-points/${id}`,
  IEC61850_TABLE_DATA: (id: number) => `/api/channels/iec61850-table-data/${id}`,
  IEC61850_DO_CHILDREN: (id: number) => `/api/channels/iec61850-do-children/${id}`,
  IEC61850_DA_CHILDREN: (id: number) => `/api/channels/iec61850-da-children/${id}`,
  IEC61850_TREE_DATA: (id: number) => `/api/channels/iec61850-tree-data/${id}`,
  IEC61850_READ_POINT: (id: number) => `/api/channels/iec61850-read-point/${id}`,
  IEC61850_WRITE_POINT: (id: number) => `/api/channels/iec61850-write-point/${id}`,
} as const;

// ===== 设备相关 =====
export const DEVICE_API = {
  LIST: '/api/devices/list',
  INFO: '/api/devices/info',
  START_SIMULATION: '/api/devices/start-simulation',
  STOP_SIMULATION: '/api/devices/stop-simulation',
  START: '/api/devices/start',
  STOP: '/api/devices/stop',
  SLAVE_ID_LIST: '/api/devices/slave-id-list',
  TABLE: '/api/devices/table',
  AUTO_READ_STATUS: '/api/devices/auto-read-status',
  START_AUTO_READ: '/api/devices/start-auto-read',
  STOP_AUTO_READ: '/api/devices/stop-auto-read',
  MANUAL_READ: '/api/devices/manual-read',
  MESSAGES: '/api/devices/messages',
  CLEAR_MESSAGES: '/api/devices/clear-messages',
  AVG_TIME: '/api/devices/avg-time',
  IEC61850_CONNECT_PROGRESS: '/api/devices/iec61850-connect-progress',
  ADD_SLAVE: '/api/devices/add-slave',
  DELETE_SLAVE: '/api/devices/delete-slave',
  EDIT_SLAVE: '/api/devices/edit-slave',
} as const;

// ===== 设备组相关 =====
export const DEVICE_GROUP_API = {
  TREE: '/api/device-groups/tree',
  LIST: '/api/device-groups/',
  ROOT: '/api/device-groups/root',
  UNGROUPED: '/api/device-groups/ungrouped',
  DETAIL: (id: number) => `/api/device-groups/${id}`,
  DEVICES_IN_GROUP: (id: number) => `/api/device-groups/${id}/devices`,
  CHILDREN: (id: number) => `/api/device-groups/${id}/children`,
  CREATE: '/api/device-groups/',
  UPDATE: (id: number) => `/api/device-groups/${id}`,
  DELETE: (id: number) => `/api/device-groups/${id}`,
  UPDATE_STATUS: (id: number) => `/api/device-groups/${id}/status`,
  ADD_DEVICE: '/api/device-groups/add-device',
  REMOVE_DEVICE: (deviceId: number) => `/api/device-groups/remove-device/${deviceId}`,
  MOVE_DEVICES: '/api/device-groups/move-devices',
  BATCH_OPERATION: (groupId: number) => `/api/device-groups/${groupId}/batch-operation`,
} as const;

// ===== 测点相关 =====
export const POINT_API = {
  EDIT_DATA: '/api/points/edit-data',
  EDIT_LIMIT: '/api/points/edit-limit',
  GET_LIMIT: '/api/points/get-limit',
  INFO: '/api/points/info',
  SET_SIMULATE_METHOD: '/api/points/set-simulate-method',
  SET_SIMULATE_STEP: '/api/points/set-simulate-step',
  SET_SIMULATION_RANGE: '/api/points/set-simulation-range',
  EDIT_METADATA: '/api/points/edit-metadata',
  EDIT_IEC104_METADATA: '/api/points/edit-iec104-metadata',
  READ_SINGLE: '/api/points/read-single',
  ADD: '/api/points/add',
  ADD_BATCH: '/api/points/add-batch',
  DELETE: '/api/points/delete',
  CHANGE_HISTORY: '/api/points/change-history',
  SET_CHANGE_TRACKING: '/api/points/set-change-tracking',
  CLEAR_CHANGE_HISTORY: '/api/points/clear-change-history',
  CLEAR_BY_SLAVE: '/api/points/clear-by-slave',
  RESET_DATA: '/api/points/reset-data',
} as const;

// ===== 测点映射相关 =====
export const POINT_MAPPING_API = {
  BASE: '/api/point-mappings',
  LIST: '/api/point-mappings/list',
  CREATE: '/api/point-mappings/create',
  UPDATE: '/api/point-mappings/update',
  DELETE: '/api/point-mappings/delete',
} as const;

// ===== 测点树相关 =====
export const POINT_TREE_API = {
  BASE: '/api/point-tree',
  TREE: '/api/point-tree/tree',
} as const;
