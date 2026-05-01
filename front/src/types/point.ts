export interface PointLimit {
  minValueLimit: number;
  maxValueLimit: number;
}

export enum PointType {
  YC = 0,
  YX = 1,
  YK = 2,
  YT = 3,
}

export const PointTypeMap = {
  "遥测": PointType.YC,
  "遥信": PointType.YX,
  "遥控": PointType.YK,
  "遥调": PointType.YT,
} as const;

export function getPointType(
  chineseName: string,
  defaultValue: PointType = PointType.YC
): PointType {
  return PointTypeMap[chineseName as keyof typeof PointTypeMap] ?? defaultValue;
}

// ===== IEC104 品质描述符定义 =====

/** IEC104 品质描述符标志位 */
export interface IEC104QualityFlags {
  ov: boolean;  // 溢出 (Overflow) — 仅遥测/遥调
  bl: boolean;  // 闭锁 (Blocked)
  sb: boolean;  // 取代 (Substituted)
  nt: boolean;  // 不刷新 (Not Topical)
  iv: boolean;  // 无效 (Invalid)
}

/** IEC104 品质描述符（包含标志位和整数值） */
export interface IEC104QualityDescriptor extends IEC104QualityFlags {
  value: number;       // 品质描述符整数值
  labels: string[];    // 激活标志的中文标签
}

/** 品质标志位掩码 */
export const IEC104_QUALITY_MASK = {
  OV: 0x01,  // 溢出
  BL: 0x02,  // 闭锁
  SB: 0x04,  // 取代
  NT: 0x08,  // 不刷新
  IV: 0x10,  // 无效
} as const;

/** 从整数值解码品质描述符 */
export function decodeIec104Quality(value: number, frameType: number): IEC104QualityDescriptor {
  const labels: string[] = [];
  const ov = frameType !== 1 && frameType !== 2 && !!(value & IEC104_QUALITY_MASK.OV);
  const bl = !!(value & IEC104_QUALITY_MASK.BL);
  const sb = !!(value & IEC104_QUALITY_MASK.SB);
  const nt = !!(value & IEC104_QUALITY_MASK.NT);
  const iv = !!(value & IEC104_QUALITY_MASK.IV);

  if (ov) labels.push("溢出");
  if (bl) labels.push("闭锁");
  if (sb) labels.push("取代");
  if (nt) labels.push("不刷新");
  if (iv) labels.push("无效");

  return { ov, bl, sb, nt, iv, value, labels };
}

/** 从品质标志位编码为整数值 */
export function encodeIec104Quality(flags: IEC104QualityFlags, frameType: number): number {
  let value = 0;
  // 遥信(1)和遥控(2)不支持 OV 标志
  if (flags.ov && frameType !== 1 && frameType !== 2) value |= IEC104_QUALITY_MASK.OV;
  if (flags.bl) value |= IEC104_QUALITY_MASK.BL;
  if (flags.sb) value |= IEC104_QUALITY_MASK.SB;
  if (flags.nt) value |= IEC104_QUALITY_MASK.NT;
  if (flags.iv) value |= IEC104_QUALITY_MASK.IV;
  return value;
}

/** 判断帧类型是否支持品质描述符 */
export function supportsQuality(frameType: number): boolean {
  return frameType !== 2; // 遥控不带品质
}

/** 判断帧类型是否支持溢出标志 */
export function supportsOverflow(frameType: number): boolean {
  return frameType === 0 || frameType === 3; // 仅遥测和遥调
}

// ===== IEC104 ASDU 类型定义 =====

export interface IEC104TypeInfo {
  type_id: string;       // 如 "M_ME_NC_1"
  type_code: number;     // 如 13
  label: string;         // 如 "短浮点遥测"
  direction: string;     // "monitoring" | "control"
  value_type: string;    // "float" | "single" | "double" | "step" | "integer"
  frame_type: number;    // 0=遥测, 1=遥信, 2=遥控, 3=遥调
  has_timestamp: boolean;
  timestamp_type: string | null;
}

/** 每种帧类型可用的 IEC104 ASDU 类型列表 */
export const IEC104_TYPES_BY_FRAME_TYPE: Record<number, IEC104TypeInfo[]> = {
  0: [ // 遥测
    { type_id: "M_ME_NC_1", type_code: 13, label: "短浮点遥测", direction: "monitoring", value_type: "float", frame_type: 0, has_timestamp: false, timestamp_type: null },
    { type_id: "M_ME_NA_1", type_code: 9, label: "归一化遥测", direction: "monitoring", value_type: "float", frame_type: 0, has_timestamp: false, timestamp_type: null },
    { type_id: "M_ME_NB_1", type_code: 11, label: "标度化遥测", direction: "monitoring", value_type: "float", frame_type: 0, has_timestamp: false, timestamp_type: null },
    { type_id: "M_ME_ND_1", type_code: 21, label: "归一化遥测(不带品质)", direction: "monitoring", value_type: "float", frame_type: 0, has_timestamp: false, timestamp_type: null },
    { type_id: "M_ME_TD_1", type_code: 34, label: "归一化遥测(CP56)", direction: "monitoring", value_type: "float", frame_type: 0, has_timestamp: true, timestamp_type: "CP56" },
    { type_id: "M_ME_TE_1", type_code: 35, label: "标度化遥测(CP56)", direction: "monitoring", value_type: "float", frame_type: 0, has_timestamp: true, timestamp_type: "CP56" },
    { type_id: "M_ME_TF_1", type_code: 36, label: "短浮点遥测(CP56)", direction: "monitoring", value_type: "float", frame_type: 0, has_timestamp: true, timestamp_type: "CP56" },
  ],
  1: [ // 遥信
    { type_id: "M_SP_NA_1", type_code: 1, label: "单点遥信", direction: "monitoring", value_type: "single", frame_type: 1, has_timestamp: false, timestamp_type: null },
    { type_id: "M_SP_TA_1", type_code: 2, label: "单点遥信(带时标)", direction: "monitoring", value_type: "single", frame_type: 1, has_timestamp: true, timestamp_type: "CP16" },
    { type_id: "M_DP_NA_1", type_code: 3, label: "双点遥信", direction: "monitoring", value_type: "double", frame_type: 1, has_timestamp: false, timestamp_type: null },
    { type_id: "M_DP_TA_1", type_code: 4, label: "双点遥信(带时标)", direction: "monitoring", value_type: "double", frame_type: 1, has_timestamp: true, timestamp_type: "CP16" },
    { type_id: "M_SP_TB_1", type_code: 30, label: "单点遥信(CP56)", direction: "monitoring", value_type: "single", frame_type: 1, has_timestamp: true, timestamp_type: "CP56" },
    { type_id: "M_DP_TB_1", type_code: 31, label: "双点遥信(CP56)", direction: "monitoring", value_type: "double", frame_type: 1, has_timestamp: true, timestamp_type: "CP56" },
  ],
  2: [ // 遥控
    { type_id: "C_SC_NA_1", type_code: 45, label: "单点遥控", direction: "control", value_type: "single", frame_type: 2, has_timestamp: false, timestamp_type: null },
    { type_id: "C_DC_NA_1", type_code: 46, label: "双点遥控", direction: "control", value_type: "double", frame_type: 2, has_timestamp: false, timestamp_type: null },
    { type_id: "C_RC_NA_1", type_code: 47, label: "步调节命令", direction: "control", value_type: "step", frame_type: 2, has_timestamp: false, timestamp_type: null },
    { type_id: "C_SC_TA_1", type_code: 58, label: "单点遥控(CP56)", direction: "control", value_type: "single", frame_type: 2, has_timestamp: true, timestamp_type: "CP56" },
    { type_id: "C_DC_TA_1", type_code: 59, label: "双点遥控(CP56)", direction: "control", value_type: "double", frame_type: 2, has_timestamp: true, timestamp_type: "CP56" },
    { type_id: "C_RC_TA_1", type_code: 60, label: "步调节命令(CP56)", direction: "control", value_type: "step", frame_type: 2, has_timestamp: true, timestamp_type: "CP56" },
  ],
  3: [ // 遥调
    { type_id: "C_SE_NC_1", type_code: 50, label: "设定值(短浮点)", direction: "control", value_type: "float", frame_type: 3, has_timestamp: false, timestamp_type: null },
    { type_id: "C_SE_NA_1", type_code: 48, label: "设定值(归一化)", direction: "control", value_type: "float", frame_type: 3, has_timestamp: false, timestamp_type: null },
    { type_id: "C_SE_NB_1", type_code: 49, label: "设定值(标度化)", direction: "control", value_type: "float", frame_type: 3, has_timestamp: false, timestamp_type: null },
    { type_id: "C_SE_TA_1", type_code: 61, label: "设定值归一化(CP56)", direction: "control", value_type: "float", frame_type: 3, has_timestamp: true, timestamp_type: "CP56" },
    { type_id: "C_SE_TB_1", type_code: 62, label: "设定值标度化(CP56)", direction: "control", value_type: "float", frame_type: 3, has_timestamp: true, timestamp_type: "CP56" },
    { type_id: "C_SE_TC_1", type_code: 63, label: "设定值短浮点(CP56)", direction: "control", value_type: "float", frame_type: 3, has_timestamp: true, timestamp_type: "CP56" },
  ],
};

/** 获取帧类型对应的默认 IEC104 类型 */
export function getDefaultIec104Type(frameType: number): string {
  const defaults: Record<number, string> = {
    0: "M_ME_NC_1",
    1: "M_SP_NA_1",
    2: "C_SC_NA_1",
    3: "C_SE_NC_1",
  };
  return defaults[frameType] || "M_ME_NC_1";
}

/** 根据 type_id 获取类型标签 */
export function getIec104TypeLabel(typeId: string | null | undefined): string {
  if (!typeId) return "";
  for (const types of Object.values(IEC104_TYPES_BY_FRAME_TYPE)) {
    const found = types.find(t => t.type_id === typeId);
    if (found) return found.label;
  }
  return typeId;
}
