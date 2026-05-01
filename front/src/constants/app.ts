/**
 * 应用通用常量
 */

// 数据刷新间隔（毫秒）
export const TABLE_REFRESH_INTERVAL = 1000;

// HTTP 请求超时时间（毫秒）
export const HTTP_TIMEOUT = 3000;

// 错误消息去重间隔（毫秒）
export const ERROR_DEBOUNCE_MS = 3000;

// 默认分页大小
export const DEFAULT_PAGE_SIZE = 10;

// 从机地址范围
export const SLAVE_ADDR_MIN = 0;
export const SLAVE_ADDR_MAX = 255;

// 端口范围
export const PORT_MIN = 1;
export const PORT_MAX = 65535;

// 侧边栏宽度
export const SIDEBAR_WIDTH = '230px';
export const SIDEBAR_COLLAPSED_WIDTH = '64px';

// 侧边栏本地存储 Key
export const LS_KEY_COLLAPSE = 'isCollapse';
export const LS_KEY_ACTIVE_ROUTE = 'activeRoute';
export const LS_KEY_THEME = 'sidebar-theme';

// 报文捕获默认限制
export const MESSAGE_DEFAULT_LIMIT = 100;

// 读取进度相关
export const READ_PROGRESS_DELAY = 1500; // 读取完成后延迟清除进度
export const SINGLE_READ_PROGRESS_DELAY = 2000;

// WebSocket 重连间隔（毫秒）
export const WS_RECONNECT_INTERVAL = 3000;
