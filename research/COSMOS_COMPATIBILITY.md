# Cosmos 7.0 / Steam 178763 离线适配

2026-09-10，针对 Steam 公开版内部版本 178763（build 25208023）。
EXE SHA-256：`8454ed80dffd5a98d7ffa747f629f6b00d95d1dde5a46a361b21bab665a3a4fb`。
PE SizeOfImage：123764736。发行版本：1.1.2-rc1；**游戏内验收尚未完成**。

本次读取本机游戏文件的代码、PE 展开信息与 RTTI，并沿游戏自身调用点核查，未启动或注入游戏。
旧 NMS.py 类型仅作为寻找入口的参考，未把其 170671 布局直接当成新版本布局。
16 个最终入口在本机文件中均有唯一的完整签名；入口匹配本身不等于游戏内运行验证。

## 入口与调用约定

| 用途 | 178763 RVA | 关键依据 |
| --- | --- | --- |
| Application Update | 0x2D7270 | 双 LEA 0x2D7458 / 0x2D747F 指向主对象 0x6E79AD0；调用后返回 0x626063 |
| GameState Update | 0x489280 | 0x2E2823 传 data+0xE70，0x2E282A 调用，时间步在 XMM1 |
| FSM StateChange | 0x2BCB1D0 | 原签名唯一匹配，保持四参数与 before/after 观察 |
| Scanner DoAction | 0x105C7F0 | cGcSimpleInteractionComponent RTTI 的 Update 路径调用；数据指针变为 +0x28，类型仍 +0x220 |
| SubmitDiscoveryData | 0x450FA0 | 扫描室类型 35 分支 0x105E581；调用 0x105E5DD 前 R8=null，R9B=1 |
| ComputeWarpCapability | 0x48A510 | 0x13306A3 原跃迁入口调用；状态仍写 result+8，1 为可跃迁 |
| 普通常规跃迁 | 0x1330480 | 原签名唯一匹配；地图内调用 0x1321FE0 / 0x13225F2，保留 check-only 与实际执行两步 |
| GalaxyMap Update | 0x13207A0 | 0x32F0B9 从 data+0x94E608 取真实地图，0x32F0C0 调用 |
| Simulation Update | 0x13A7380 | Update lambda RTTI 的 vtable 0x4AC2FC0 回溯；0x33574A 使用 mode=0，返回位置 0x33574F |
| IsOnboardOwnFreighter | 0x12444A0 | 原签名唯一匹配；能力检查 0x48A988 传 data+0x57A100 |
| 候选空间查询 | 0x1352770 | 原签名唯一匹配，保留原五参数和 64 字节结果 |
| SelectStar | 0x132AD00 | 选中对象仍为 map+0x1BB0；原始查询 +0x20；后续调用同一能力检查 |
| 星间距离 | 0x13508D0 | 原签名唯一匹配，原跃迁入口 0x1330513 调用 |
| ClassifyStarKeyAttributes | 0x13191E0 | 原签名唯一匹配，0x13196F0 的颜色/种族/废弃/海盗字段写入位置核对 |
| UploadAndRewardAll | 0x757BD0 | 同名 lambda RTTI；真实 UI 处理器 0x7564C5 调用 |
| FSM 排队开图 | 0x2BCB2A0 | 完整 64 字节核查，队列与 owner 偏移不变；空标记 0x34FE6C0 |

**发现提交现在是四参数：** `bool(manager, record, locally_new, bool)`。
0x450FB8 保存 R9B，0x450FCA 把它传给内部处理函数；扫描室显式传 true。
Python 直接绑定和 observer trampoline 均同步改为四参数。沿用旧三参数会遗漏原生必需参数。

## 数据布局

- ApplicationData 的构造调用 0x2C6AF3～0x2C6B2A 证明 GameState +0xE70、Simulation +0x4CCF90、Frontend +0x849050。
- Simulation Update 0x13A7549 写 CurrentUA +0x255900；太阳系指针仍 +0x24DFD0。
- 扫描室 0x105E58A～0x105E5DD 证明太阳系行星数 +0x2544、发现记录起点 +0x2E38、行星步长 0xD9170、发现管理器 data+0x2CE838。
- 普通跃迁仍使用货船内部枚举 10。环境位于 data+0x57A100，当前位置 +0x468、稳定位置 +0x474。原生能力/跃迁路径比较 data+0x57A574；例如 0x55DE72 比较 data+0x57A568 与 10。
- 0x1330A72 写普通货船跃迁请求 data+0x71F1A0=3；Simulation 过渡对象仍 +0x24DFF0，即 data+0x71AF80。
- 暂停标记 application+0xB8D5；例如 0x2D76B5 将该标记作为更新暂停条件。
- 银河地图基础 query、时钟、选中结果、缓存就绪字段不变。面板子对象仍 map+0x21C0，query +0x5D0 / 已处理 query +0x5C0，状态仍 map+0x26C4；选择标志改为 map+0x315B（父面板 +0xFCB）。
- 0x134D28D 读取子面板颜色 +0xF50，0x134D2A6 调用光谱格式化 0x1640C10。该函数 276 条指令的运算/寄存器序列与旧实现一致（重定位引用已单独核对），保留纯 Python 算法，不新增原生格式化调用。
- 0x134C387 / 0x134C3BE / 0x134C3CB / 0x134C413 写四个标签标志；新起点 +0xF80。渲染依旧按气态巨行星、蠕虫、不协、水的优先级显示。
- Frontend 构造 0x2CB66E 写 vtable 0x4A34070；0x2CB8C0 构造页面 +0x2BA0；页面构造 0x2CC73B 构造发现页 +0xB70，与上传 UI 调用一致。上传奖励字段总计 +0x7600，提示仍 +0x7C；待上传集合布局及 17 类计数保持不变。

## 加载保护与验收边界

扫描前新增：自己的货船、当前位置与稳定位置均为内部、游戏未暂停、无跃迁请求或过渡；满足后还需连续世界更新 3 秒。中途状态失效、F1 暂停或世界帧间隔超过 0.5 秒会重新计时。每次行星提交前复查这些条件，失败即停止。参数在 single_trial.json。

这些检查没有检测碰撞体是否恢复，**不能据此宣称已解决此前长时间运行中穿出货船的问题**。也没有修改玩家位置、游戏物理或存档。

已完成 102 项非游戏测试和本机入口核查。待用户使用备份后的测试存档依次验收：一次跃迁和扫描、两轮循环、F1/F2/F3、六组筛选、上传、后台。旧版 170671 的运行日志只作历史记录，发行元数据明确标记 runtime_verified=false。
