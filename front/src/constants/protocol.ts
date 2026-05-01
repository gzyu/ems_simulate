/**
 * 协议与通道相关常量
 */

// 协议类型枚举值（与后端对齐）
export const PROTOCOL_TYPE = {
  MODBUS_RTU: 0,
  MODBUS_TCP: 1,
  IEC104: 2,
  DLT645: 3,
  IEC61850: 4,
} as const;

// 连接类型枚举值
export const CONN_TYPE = {
  SERIAL_MASTER: 0,
  TCP_CLIENT: 1,
  TCP_SERVER: 2,
  SERIAL_SLAVE: 3,
} as const;

// 协议默认端口映射
export const PROTOCOL_DEFAULT_PORTS: Record<number, number> = {
  [PROTOCOL_TYPE.MODBUS_RTU]: 502,
  [PROTOCOL_TYPE.MODBUS_TCP]: 502,
  [PROTOCOL_TYPE.IEC104]: 2404,
  [PROTOCOL_TYPE.DLT645]: 502,
  [PROTOCOL_TYPE.IEC61850]: 102,
} as const;

// 标准波特率列表
export const BAUD_RATES = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200] as const;

// IEC61850 分类列表
export const IEC61850_CATEGORIES: ReadonlyArray<{ key: string; label: string }> = [
  { key: 'GOOSE', label: 'GOOSE' },
  { key: 'Reports', label: 'Reports' },
  { key: 'SettingGroups', label: 'SettingGroups' },
  { key: 'Files', label: 'Files' },
  { key: 'DataSets', label: 'DataSets' },
  { key: 'Data Model', label: 'Data Model' },
] as const;

// 判断 IEC61850 协议的字符串标识
export const IEC61850_PROTOCOL_NAMES = ['Iec61850Client', 'Iec61850Server'] as const;

// 判断 IEC104 协议的字符串标识
export const IEC104_PROTOCOL_NAMES = ['Iec104Client', 'Iec104Server'] as const;

// 判断是否为 IEC61850 协议
export function isIec61850Protocol(protocolStr: string | number): boolean {
  return IEC61850_PROTOCOL_NAMES.includes(protocolStr as any);
}

// 判断是否为 IEC104 协议
export function isIec104Protocol(protocolStr: string | number): boolean {
  return IEC104_PROTOCOL_NAMES.includes(protocolStr as any);
}
