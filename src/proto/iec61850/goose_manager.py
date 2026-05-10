"""
IEC 61850 GOOSE 管理器

统一管理所有 GOOSE Publisher 和 Receiver 实例，
提供 CRUD、启停控制、状态查询等功能。

设计模式: Singleton + Manager，通过 app.state.goose_manager 挂载到 FastAPI。
"""

import uuid
from typing import Any, Dict, List, Optional

from src.proto.iec61850.log import log

try:
    from src.proto.iec61850.goose_publisher import (
        GoosePublisher,
        GooseDataSetEntry,
        HAS_IEC61850,
    )
    from src.proto.iec61850.goose_subscriber import (
        GooseReceiver,
        GooseSubscription,
        HAS_IEC61850 as HAS_IEC61850_SUB,
    )
    GOOSE_AVAILABLE = HAS_IEC61850 and HAS_IEC61850_SUB
except ImportError:
    GOOSE_AVAILABLE = False
    log.warning("pyiec61850 未安装，GOOSE 功能不可用")


class GooseManager:
    """GOOSE 资源管理器

    管理 GOOSE Publisher 和 Receiver 的完整生命周期。
    """

    def __init__(self):
        self._publishers: Dict[str, GoosePublisher] = {}   # id -> publisher
        self._receivers: Dict[str, GooseReceiver] = {}     # id -> receiver

        # go_cb_ref -> publisher_id 映射 (用于快速查找)
        self._gocbref_to_pid: Dict[str, str] = {}
        # interface -> receiver_id 映射
        self._interface_to_rid: Dict[str, str] = {}

        # go_cb_ref -> channel_id 映射 (用于持久化)
        self._channel_map: Dict[str, int] = {}

    # ===== Publisher 管理 =====

    def create_publisher(
        self,
        interface: str = "eth0",
        go_cb_ref: str = "",
        go_id: str = "",
        data_set_ref: str = "",
        app_id: int = 0x0001,
        conf_rev: int = 1,
        time_allowed_to_live: int = 1000,
        dst_mac: Optional[List[int]] = None,
        vlan_id: int = 0,
        vlan_prio: int = 4,
        simulation: bool = True,
        entries: Optional[List[Dict[str, Any]]] = None,
        server: Optional[Any] = None,
        channel_id: Optional[int] = None,
        force_recreate: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """创建 GOOSE Publisher

        Args:
            interface: 网络接口
            go_cb_ref: GOOSE 控制块引用
            go_id: GOOSE 标识符
            data_set_ref: 数据集引用
            app_id: APPID
            conf_rev: 配置修订号
            time_allowed_to_live: 存活时间 (ms)
            dst_mac: 目标 MAC 地址
            vlan_id: VLAN ID
            vlan_prio: VLAN 优先级
            simulation: 仿真模式
            entries: 数据集条目
            server: IEC61850Server 实例，提供后会在 MMS 数据模型中创建 GSEControlBlock 节点
            channel_id: 关联的通道 ID，提供后会持久化到数据库
            force_recreate: 如果为 True，当 go_cb_ref 已存在时先删除再重新创建
        """
        if not GOOSE_AVAILABLE:
            log.error("GOOSE 功能不可用 (pyiec61850 未安装)")
            return None

        # 检查 go_cb_ref 是否已存在
        if go_cb_ref and go_cb_ref in self._gocbref_to_pid:
            if force_recreate:
                log.info(f"GOOSE Publisher 已存在但强制重新创建: go_cb_ref={go_cb_ref}")
                self.delete_publisher(go_cb_ref, delete_from_db=False)
            else:
                existing_id = self._gocbref_to_pid[go_cb_ref]
                log.warning(f"GOOSE Publisher 已存在: go_cb_ref={go_cb_ref}, id={existing_id}")
                return self.get_publisher_status(existing_id)

        try:
            publisher = GoosePublisher(
                interface=interface,
                go_cb_ref=go_cb_ref,
                go_id=go_id,
                data_set_ref=data_set_ref,
                app_id=app_id,
                conf_rev=conf_rev,
                time_allowed_to_live=time_allowed_to_live,
                dst_mac=dst_mac,
                vlan_id=vlan_id,
                vlan_prio=vlan_prio,
                simulation=simulation,
            )

            # 添加数据集条目
            if entries:
                for e in entries:
                    entry = GooseDataSetEntry(
                        name=e.get("name", ""),
                        value=e.get("value"),
                        iec_type=e.get("iec_type", "boolean"),
                    )
                    publisher.add_entry(entry)

            # 生成唯一 ID
            pub_id = go_cb_ref or str(uuid.uuid4())
            self._publishers[pub_id] = publisher
            if go_cb_ref:
                self._gocbref_to_pid[go_cb_ref] = pub_id

            # 持久化到数据库
            if channel_id is not None:
                self._channel_map[go_cb_ref] = channel_id
                self.save_to_db(channel_id, go_cb_ref)

            # 注册 GSEControlBlock 到 MMS 数据模型（同时传递 entries 用于 DataSet FCDA）
            if server is not None:
                try:
                    gse_name = go_cb_ref.split("$")[-1] if "$" in go_cb_ref else go_cb_ref.split("/")[-1]
                    # 从 go_cb_ref 中提取 LD 实例名
                    # go_cb_ref 格式: "LD0/LLN0$GO$gcb1" -> ld_inst="LD0"
                    go_ld_inst = go_cb_ref.split("/")[0] if "/" in go_cb_ref else None
                    server.add_goose_control_block(
                        name=gse_name,
                        app_id=app_id,
                        data_set_ref=data_set_ref,
                        conf_rev=conf_rev,
                        go_id=go_id,
                        min_time=10,
                        max_time=time_allowed_to_live,
                        ld_inst=go_ld_inst,
                        entries=entries,
                    )
                except Exception as e:
                    log.warning(f"注册 GSEControlBlock 到 MMS 模型失败: {e}")

            log.info(f"GOOSE Publisher 创建成功: id={pub_id}, go_cb_ref={go_cb_ref}")
            return self.get_publisher_status(pub_id)
        except Exception as e:
            log.error(f"创建 GOOSE Publisher 异常: {e}")
            return None

    def list_publishers(self) -> List[Dict[str, Any]]:
        """列出所有 Publisher 状态"""
        return [
            self.get_publisher_status(pid) or {"id": pid, "error": "状态获取失败"}
            for pid in self._publishers
        ]

    def get_publisher_status(self, publisher_id: str) -> Optional[Dict[str, Any]]:
        """获取 Publisher 状态"""
        publisher = self._publishers.get(publisher_id)
        if not publisher:
            return None

        status = publisher.get_status()
        status["id"] = publisher_id
        status["entries"] = publisher.get_entries()
        return status

    def update_publisher(
        self,
        publisher_id: str,
        go_id: Optional[str] = None,
        conf_rev: Optional[int] = None,
        time_allowed_to_live: Optional[int] = None,
        simulation: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """更新 Publisher 配置 (仅未运行时)"""
        publisher = self._publishers.get(publisher_id)
        if not publisher:
            return None

        if publisher.is_running:
            log.warning(f"GOOSE Publisher 运行中，无法更新: {publisher_id}")
            return None

        if go_id is not None:
            publisher.go_id = go_id
        if conf_rev is not None:
            publisher.conf_rev = conf_rev
        if time_allowed_to_live is not None:
            publisher.time_allowed_to_live = time_allowed_to_live
        if simulation is not None:
            publisher.simulation = simulation

        # 持久化更新
        self._auto_persist(publisher_id)

        return self.get_publisher_status(publisher_id)

    def delete_publisher(self, publisher_id: str, delete_from_db: bool = False) -> bool:
        """删除 Publisher

        Args:
            publisher_id: Publisher ID (go_cb_ref)
            delete_from_db: 是否同时从数据库删除持久化记录
        """
        publisher = self._publishers.get(publisher_id)
        if not publisher:
            return False

        publisher.stop()
        go_cb_ref = publisher.go_cb_ref
        del self._publishers[publisher_id]
        if go_cb_ref in self._gocbref_to_pid:
            del self._gocbref_to_pid[go_cb_ref]
        if go_cb_ref in self._channel_map:
            del self._channel_map[go_cb_ref]

        # 从数据库删除
        if delete_from_db:
            try:
                from src.data.dao.goose_publisher_dao import GoosePublisherDao
                GoosePublisherDao.delete_publisher_by_go_cb_ref(go_cb_ref)
                log.info(f"GOOSE Publisher 已从数据库删除: id={publisher_id}")
            except Exception as e:
                log.warning(f"从数据库删除 GOOSE Publisher 失败: {e}")

        log.info(f"GOOSE Publisher 已删除: id={publisher_id}")
        return True

    def start_publisher(self, publisher_id: str) -> bool:
        """启动 Publisher"""
        publisher = self._publishers.get(publisher_id)
        if not publisher:
            return False
        return publisher.start()

    def stop_publisher(self, publisher_id: str) -> bool:
        """停止 Publisher"""
        publisher = self._publishers.get(publisher_id)
        if not publisher:
            return False
        publisher.stop()
        return True

    def publish_now(self, publisher_id: str) -> bool:
        """立即发布 GOOSE 报文"""
        publisher = self._publishers.get(publisher_id)
        if not publisher:
            return False
        return publisher.publish()

    # ===== Publisher 数据集管理 =====

    def add_publisher_entry(
        self,
        publisher_id: str,
        name: str,
        value: Any = None,
        iec_type: str = "boolean",
    ) -> Optional[Dict[str, Any]]:
        """向 Publisher 添加数据集条目"""
        publisher = self._publishers.get(publisher_id)
        if not publisher:
            return None

        entry = GooseDataSetEntry(name=name, value=value, iec_type=iec_type)
        publisher.add_entry(entry)

        # 持久化
        self._auto_persist(publisher_id)

        return {"publisher_id": publisher_id, "entry_count": len(publisher.get_entries())}

    def update_publisher_entry(
        self,
        publisher_id: str,
        index: int,
        value: Any,
    ) -> Optional[bool]:
        """更新 Publisher 数据集条目值"""
        publisher = self._publishers.get(publisher_id)
        if not publisher:
            return None

        result = publisher.update_entry(index, value)

        # 持久化
        self._auto_persist(publisher_id)

        return result

    def remove_publisher_entry(self, publisher_id: str, index: int) -> bool:
        """移除 Publisher 数据集条目"""
        publisher = self._publishers.get(publisher_id)
        if not publisher:
            return False
        publisher.remove_entry(index)

        # 持久化
        self._auto_persist(publisher_id)

        return True

    def _auto_persist(self, go_cb_ref: str) -> None:
        """自动将 Publisher 持久化到数据库（如果有 channel_id 映射）"""
        channel_id = self._channel_map.get(go_cb_ref)
        if channel_id is not None:
            try:
                self.save_to_db(channel_id, go_cb_ref)
            except Exception as e:
                log.warning(f"自动持久化 GOOSE Publisher 失败: {e}")

    # ===== 持久化管理 =====

    def save_to_db(self, channel_id: int, go_cb_ref: str) -> bool:
        """将 Publisher 配置持久化到数据库

        Args:
            channel_id: 通道 ID
            go_cb_ref: GOOSE 控制块引用
        """
        try:
            from src.data.dao.goose_publisher_dao import GoosePublisherDao

            status = self.get_publisher_status(go_cb_ref)
            if not status:
                log.warning(f"save_to_db 失败: Publisher 未找到 go_cb_ref={go_cb_ref}")
                return False

            db_id = GoosePublisherDao.save_publisher(channel_id, status)
            if db_id:
                log.info(f"GOOSE Publisher 已持久化: go_cb_ref={go_cb_ref}, db_id={db_id}")
                return True
            return False
        except Exception as e:
            log.error(f"持久化 GOOSE Publisher 失败: {e}")
            return False

    def delete_publisher_from_db(self, go_cb_ref: str) -> bool:
        """从数据库删除 Publisher 持久化记录"""
        try:
            from src.data.dao.goose_publisher_dao import GoosePublisherDao
            result = GoosePublisherDao.delete_publisher_by_go_cb_ref(go_cb_ref)
            if result:
                log.info(f"GOOSE Publisher 已从数据库删除: {go_cb_ref}")
            return result
        except Exception as e:
            log.error(f"从数据库删除 GOOSE Publisher 失败: {e}")
            return False

    def delete_all_by_channel(self, channel_id: int) -> int:
        """删除通道下所有 GOOSE Publisher 持久化记录"""
        try:
            from src.data.dao.goose_publisher_dao import GoosePublisherDao
            count = GoosePublisherDao.delete_by_channel(channel_id)
            log.info(f"已删除通道 {channel_id} 的 {count} 个 GOOSE Publisher 持久化记录")
            return count
        except Exception as e:
            log.error(f"删除通道 GOOSE Publisher 持久化记录失败: {e}")
            return 0

    def load_from_db(self, channel_id: Optional[int] = None, server: Optional[Any] = None) -> int:
        """从数据库加载 GOOSE Publisher 到内存

        Args:
            channel_id: 可选，只加载指定通道的 Publisher
            server: 可选，IEC61850Server 实例

        Returns:
            加载的 Publisher 数量
        """
        try:
            from src.data.dao.goose_publisher_dao import GoosePublisherDao

            if channel_id is not None:
                configs = GoosePublisherDao.get_by_channel(channel_id)
            else:
                configs = GoosePublisherDao.get_all()

            loaded_count = 0
            for cfg in configs:
                go_cb_ref = cfg.get("go_cb_ref", "")
                if not go_cb_ref:
                    continue

                # 跳过已加载的
                if go_cb_ref in self._gocbref_to_pid:
                    continue

                try:
                    # 恢复内存中的 Publisher
                    publisher = GoosePublisher(
                        interface=cfg.get("interface", "eth0"),
                        go_cb_ref=go_cb_ref,
                        go_id=cfg.get("go_id", ""),
                        data_set_ref=cfg.get("data_set_ref", ""),
                        app_id=cfg.get("app_id", 0x0001),
                        conf_rev=cfg.get("conf_rev", 1),
                        time_allowed_to_live=cfg.get("time_allowed_to_live", 1000),
                        dst_mac=cfg.get("dst_mac"),
                        vlan_id=cfg.get("vlan_id", 0),
                        vlan_prio=cfg.get("vlan_prio", 4),
                        simulation=cfg.get("simulation", True),
                    )

                    # 添加数据集条目（跳过重复名称，兼容旧数据）
                    seen_names: set = set()
                    for e in cfg.get("entries", []):
                        name = e.get("name", "")
                        if not name or name in seen_names:
                            if name:
                                log.warning(f"数据库加载时跳过重复的条目名称: {name}")
                            continue
                        seen_names.add(name)
                        entry = GooseDataSetEntry(
                            name=name,
                            value=e.get("value"),
                            iec_type=e.get("iec_type", "boolean"),
                        )
                        publisher.add_entry(entry)

                    # 注册到管理器
                    pub_id = go_cb_ref
                    self._publishers[pub_id] = publisher
                    self._gocbref_to_pid[go_cb_ref] = pub_id

                    # 记录 channel_id 映射
                    db_channel_id = cfg.get("_channel_id")
                    if db_channel_id is not None:
                        self._channel_map[go_cb_ref] = db_channel_id

                    # 注册 GSEControlBlock
                    if server is not None:
                        try:
                            gse_name = go_cb_ref.split("$")[-1] if "$" in go_cb_ref else go_cb_ref.split("/")[-1]
                            # 从 go_cb_ref 中提取 LD 实例名
                            go_ld_inst = go_cb_ref.split("/")[0] if "/" in go_cb_ref else None
                            server.add_goose_control_block(
                                name=gse_name,
                                app_id=cfg.get("app_id", 0x0001),
                                data_set_ref=cfg.get("data_set_ref", ""),
                                conf_rev=cfg.get("conf_rev", 1),
                                go_id=cfg.get("go_id", ""),
                                min_time=10,
                                max_time=cfg.get("time_allowed_to_live", 1000),
                                ld_inst=go_ld_inst,
                            )
                        except Exception as gse_err:
                            log.warning(f"从数据库恢复时注册 GSEControlBlock 失败: {gse_err}")

                    loaded_count += 1
                except Exception as e:
                    log.error(f"从数据库恢复 Publisher 失败: {go_cb_ref}, {e}")

            log.info(f"从数据库加载了 {loaded_count} 个 GOOSE Publisher")
            return loaded_count
        except Exception as e:
            log.error(f"从数据库加载 GOOSE Publisher 失败: {e}")
            return 0

    # ===== Receiver 管理 =====

    def create_receiver(
        self,
        interface: str = "eth0",
        subscriptions: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """创建 GOOSE Receiver"""
        if not GOOSE_AVAILABLE:
            log.error("GOOSE 功能不可用 (pyiec61850 未安装)")
            return None

        # 检查同一接口是否已有 Receiver
        if interface in self._interface_to_rid:
            existing_id = self._interface_to_rid[interface]
            log.warning(f"GOOSE Receiver 已存在: interface={interface}, id={existing_id}")
            return self.get_receiver_status(existing_id)

        try:
            receiver = GooseReceiver(interface=interface)

            # 添加初始订阅
            if subscriptions:
                for s in subscriptions:
                    receiver.add_subscription(
                        go_cb_ref=s.get("go_cb_ref", ""),
                        app_id=s.get("app_id"),
                        dst_mac=s.get("dst_mac"),
                        description=s.get("description", ""),
                    )

            # 使用接口名作为 ID
            recv_id = interface
            self._receivers[recv_id] = receiver
            self._interface_to_rid[interface] = recv_id

            log.info(f"GOOSE Receiver 创建成功: id={recv_id}, interface={interface}")
            return self.get_receiver_status(recv_id)
        except Exception as e:
            log.error(f"创建 GOOSE Receiver 异常: {e}")
            return None

    def list_receivers(self) -> List[Dict[str, Any]]:
        """列出所有 Receiver 状态"""
        return [
            self.get_receiver_status(rid) or {"id": rid, "error": "状态获取失败"}
            for rid in self._receivers
        ]

    def get_receiver_status(self, receiver_id: str) -> Optional[Dict[str, Any]]:
        """获取 Receiver 状态"""
        receiver = self._receivers.get(receiver_id)
        if not receiver:
            return None

        status = receiver.get_status()
        status["id"] = receiver_id
        return status

    def delete_receiver(self, receiver_id: str) -> bool:
        """删除 Receiver"""
        receiver = self._receivers.get(receiver_id)
        if not receiver:
            return False

        interface = receiver.interface
        receiver.stop()
        del self._receivers[receiver_id]
        if interface in self._interface_to_rid:
            del self._interface_to_rid[interface]

        log.info(f"GOOSE Receiver 已删除: id={receiver_id}")
        return True

    def start_receiver(self, receiver_id: str) -> bool:
        """启动 Receiver"""
        receiver = self._receivers.get(receiver_id)
        if not receiver:
            return False
        return receiver.start()

    def stop_receiver(self, receiver_id: str) -> bool:
        """停止 Receiver"""
        receiver = self._receivers.get(receiver_id)
        if not receiver:
            return False
        receiver.stop()
        return True

    # ===== Receiver 订阅管理 =====

    def add_subscription(
        self,
        receiver_id: str,
        go_cb_ref: str,
        app_id: Optional[int] = None,
        dst_mac: Optional[List[int]] = None,
        description: str = "",
    ) -> Optional[Dict[str, Any]]:
        """向 Receiver 添加订阅"""
        receiver = self._receivers.get(receiver_id)
        if not receiver:
            return None

        if receiver.is_running:
            log.warning(f"GOOSE Receiver 运行中，无法添加订阅: {receiver_id}")
            return None

        sub = receiver.add_subscription(go_cb_ref, app_id, dst_mac, description)
        return sub.to_dict()

    def remove_subscription(self, receiver_id: str, go_cb_ref: str) -> bool:
        """从 Receiver 移除订阅"""
        receiver = self._receivers.get(receiver_id)
        if not receiver:
            return False

        if receiver.is_running:
            log.warning(f"GOOSE Receiver 运行中，无法移除订阅: {receiver_id}")
            return False

        return receiver.remove_subscription(go_cb_ref)

    # ===== 全局管理 =====

    def stop_all(self) -> None:
        """停止所有 Publisher 和 Receiver"""
        for publisher in self._publishers.values():
            publisher.stop()
        for receiver in self._receivers.values():
            receiver.stop()
        log.info("所有 GOOSE 资源已停止")

    def get_all_status(self) -> Dict[str, Any]:
        """获取所有 GOOSE 资源状态概览"""
        return {
            "goose_available": GOOSE_AVAILABLE,
            "publisher_count": len(self._publishers),
            "receiver_count": len(self._receivers),
            "publishers": self.list_publishers(),
            "receivers": self.list_receivers(),
        }


# 全局单例
_goose_manager: Optional[GooseManager] = None


def get_goose_manager() -> GooseManager:
    """获取全局 GooseManager 单例"""
    global _goose_manager
    if _goose_manager is None:
        _goose_manager = GooseManager()
    return _goose_manager
