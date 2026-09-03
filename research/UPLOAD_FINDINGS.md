# “上传全部发现”原生入口核查

核查对象仅为固定 SHA-256 `ea7e5a29bbf931f96ae8dda81553353aab3431dc38e4ed7dfb66bdce100770e3` 的 NMS.exe，过程为离线读取文件，没有调用游戏、写内存或读取存档。

- MSVC RTTI 字符串包含 `UploadAndRewardAll@cGcFrontendPageDiscovery` 的 lambda；其函数对象 vtable 位于 RVA `0x47BC0D0`，调用槽指向 lambda 实现。
- 游戏 UI 的 `UPLOAD_ALL` / `UI_UPLOAD_ALL` 分支位于 RVA `0x704910`。该分支唯一一次直接调用 RVA `0x706060`，传入真实发现页数据 `frontend + 0x2790 + 0xB30`。
- RVA `0x706060` 遍历发现管理器中“可上传”集合，把条目状态设为 1，按 17 种记录类型计算奖励，转移/处理条目、清空可上传集合，并更新 UI 奖励字段。这与函数名 `UploadAndRewardAll` 一致。
- 前端管理器构造函数 RVA `0x2CF930` 设置 vtable RVA `0x479AC40`，并构造 `frontend + 0x2790` 的页面容器。运行时因此使用游戏的真实嵌入对象，不创建不完整的替代结构。
- `native/upload.json` 保存入口、对象布局和检查上限。运行时在主 application update 调用路径、APPVIEW、刚跃迁目标、自己的货船、无暂停/跃迁/界面队列时，逐条验证待上传记录指针和类型，再做唯一一次调用。

反汇编范围保存在 `research/upload-all-disassembly.txt`。当前 EXE 的入口签名唯一匹配。静态核查本身不能证明网络服务最终接受上传，因此运行日志只确认游戏原生处理函数接收并清空了本地可上传集合。

2026-09-04 的测试存档实测补齐了行为验证：自动扫描 5 个行星后，原生批量处理接收 16 条待上传记录；用户在游戏中确认上传成功，无失败或丢弃事件。摘要见 `research/upload-trial-20260904T0100.json`。
