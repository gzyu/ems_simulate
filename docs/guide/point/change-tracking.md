# 变化回溯

本章节介绍测点值的变化回溯功能。系统会记录测点值发生变化的原因和详细信息，以便于随时排查问题和追踪数据来源。

## 变化原因说明

系统记录的测点变化原因主要包括以下几种，每种原因不仅代表了值的更新源，还反映了系统的不同交互场景：

- **manual**: 手动置值
  
  - 场景：用户通过 Web 界面或命令行工具手动输入或修改了该测点的值。
  
    >![](/doc-img/point/change-tracking/1.png)
  
- **simulation**: 自动模拟
  
  - 场景：该测点配置了模拟规则（如随机数、正弦波等），系统后台运行模拟任务更新了它的值。
  
    >![](/doc-img/point/change-tracking/2.png)
  
- **mapping**: 关联测点更新
  
  - 场景：该测点的状态或数值受到其它配置了映射关系的测点影响，同步完成了联动更新。
  
    举例：yc1=yc2*10+5
  
    >![](/doc-img/point/change-tracking/3.png)
  
    查看变化回溯
  
    >![](/doc-img/point/change-tracking/4.png)
  
- **protocol**: 协议远程修改
  
  - 场景：作为从机运行时，来自主站（如客户端 SCADA 系统）通过通讯协议（Modbus 等）对当前测点发起了写操作。
  
    >![](/doc-img/point/change-tracking/5.png)
  
- **client_read**: 客户端读取
  
  - 场景：作为主机运行时，EMS Simulate 模拟器从真实下级设备成功读取到了测点最新数据并完成同步。
  
    >![](/doc-img/point/change-tracking/6.png)
