/**
 * 表格相关常量
 * 集中管理表格列名、列宽、筛选选项等配置
 */

import { PointType } from '@/types/point';
import { IEC104_TYPES_BY_FRAME_TYPE } from '@/types/point';

// ===== 寄存器解析码分类 =====

export const INT_REGISTER_DECODE_LIST = [
  '0x10', '0x11', '0x20', '0x21', '0x22', '0xB0', '0xB1', '0xC0', '0xC1',
] as const;

export const LONG_REGISTER_DECODE_LIST = [
  '0x40', '0x41', '0x43', '0x44', '0xD0', '0xD1', '0xD4', '0xD5', '0x60', '0x61', '0xE0', '0xE1',
] as const;

export const FLOAT_REGISTER_DECODE_LIST = [
  '0x42', '0x45', '0xD2', '0xD3', '0x62', 'E2',
] as const;

// ===== 表格列名映射 =====

export const TABLE_COLUMN_NAMES = {
  ADDRESS: '地址',
  HEX_ADDRESS: '16进制地址',
  POINT_CODE: '测点编码',
  POINT_NAME: '测点名称',
  REGISTER_VALUE: '寄存器值',
  REAL_VALUE: '真实值',
  MUL_COE: '乘法系数',
  ADD_COE: '加法系数',
  BIT: '位',
  FUNC_CODE: '功能码',
  DECODE_CODE: '解析码',
  FRAME_TYPE: '帧类型',
  IEC104_TYPE: 'IEC104类型',
  TYPE_ID: '类型标识',
  CAUSE: '传送原因',
  COMMON_ADDRESS: '公共地址',
  INFO_ADDRESS: '信息体地址',
  DATA_ID: '数据标识',
  DATA_LENGTH: '数据长度',
  STATUS: '状态',
} as const;

// ===== 列宽度映射 =====

export const COLUMN_WIDTH_MAP: Record<string, number> = {
  '测点编码': 150,
  '测点名称': 200,
  '寄存器值': 120,
  '真实值': 120,
  '乘法系数': 100,
  '加法系数': 100,
  '位': 50,
  '功能码': 90,
  '解析码': 90,
  '帧类型': 80,
  'IEC104类型': 160,
  '类型标识': 100,
  '传送原因': 100,
  '公共地址': 100,
  '信息体地址': 120,
  '数据标识': 120,
  '数据长度': 80,
  '状态': 80,
  'default': 100,
} as const;

// ===== 帧类型筛选选项 =====

export const FRAME_TYPE_FILTERS = [
  { text: '遥测', value: PointType.YC },
  { text: '遥信', value: PointType.YX },
  { text: '遥控', value: PointType.YK },
  { text: '遥调', value: PointType.YT },
] as const;

// ===== IEC104 类型筛选选项（从 point.ts 中的定义自动生成） =====

export const IEC104_TYPE_FILTERS: Array<{ text: string; value: string }> = [];

// 自动从 IEC104_TYPES_BY_FRAME_TYPE 生成筛选项
for (const types of Object.values(IEC104_TYPES_BY_FRAME_TYPE)) {
  for (const t of types) {
    const existing = IEC104_TYPE_FILTERS.find(f => f.value === t.label);
    if (!existing) {
      IEC104_TYPE_FILTERS.push({ text: t.label, value: t.label });
    }
  }
}

// ===== 帧类型标签颜色映射 =====

export const FRAME_TYPE_TAG_MAP: Record<string, string> = {
  '遥测': 'success',
  '遥信': 'warning',
  '遥控': 'danger',
  '遥调': 'info',
} as const;

// ===== IEC104 类型标签颜色 =====

export function getIec104TagType(label: string): string {
  if (label.includes('遥测')) return 'success';
  if (label.includes('遥信')) return 'warning';
  if (label.includes('遥控')) return 'danger';
  if (label.includes('遥调') || label.includes('设定值')) return 'info';
  if (label.includes('步调节')) return 'danger';
  return 'info';
}

// ===== 提示文本 =====

export const DECODE_CODE_TOOLTIP = '解析码说明: 16位(0x20/21/C0/C1), 32位整(0x40/41/D0/D1), 32位浮(0x42/D2), 64位(0x60/61/E0/E1)';
export const FUNC_CODE_TOOLTIP = '01:读线圈(可读写→05写) 02:读离散输入(只读) 03:读保持寄存器(可读写→06写) 04:读输入寄存器(只读)';

// ===== 客户端协议标识 =====

export const CLIENT_PROTOCOL_NAMES = ['ModbusTcpClient', 'ModbusRtuClient', 'Iec104Client', 'Dlt645Client', 'Iec61850Client'] as const;
