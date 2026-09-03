# 无人深空原生自动探索器 1.0.0

[![tests](https://github.com/yuhan2680/NoMansSkyScanner/actions/workflows/tests.yml/badge.svg)](https://github.com/yuhan2680/NoMansSkyScanner/actions/workflows/tests.yml)

当前版本针对 Steam 公开版《无人深空》内部版本 **170671**。程序使用游戏自己的函数完成：自动打开普通银河地图、伪随机选择可达恒星、货船跃迁、等待真实加载状态、扫描整个新恒星系，以及可选的“上传全部发现”。它不发送键盘或鼠标输入，不依赖扫描室位置，不读取或修改存档。

## 启动

1. 先备份存档并使用测试存档。完全退出上一次游戏进程后，重新进入存档，角色站在自己的货船内部，关闭游戏内菜单。
2. 双击发行包中的 `NoMansSkyScanner.exe`。在源码目录中也可双击 `start_explorer.bat`。
3. 等命令行显示“循环已就绪”后使用热键：

| 热键 | 功能 |
|---|---|
| **F1** | 第一次按下启动；之后按下暂停或继续 |
| **F2** | 开关自动上传；每次启动默认为关闭 |
| **F3** | 停止本次会话的后续动作 |

F2 开启后，每个恒星系扫描完毕，程序会调用游戏原生的“上传全部”处理。这会处理当前存档中全部符合条件的待上传发现，包括以前积累的发现。命令行确认的是游戏本地批量处理函数已经接收；网络服务最终是否收到仍由游戏自身决定。关闭 F2 会阻止尚未发起的下一次批量上传，不能撤销已提交给游戏的请求。

F3 会在下一次原生调用前阻止动作。已经排队的开图或已开始的跃迁无法撤销。停止、完成或报错后 F1 不会重启同一会话。退出游戏才能完全卸载运行时；必须重新启动游戏后才能再次运行启动器。

## 已验证范围

- 自动单次：1 次跃迁、1 次整系扫描、6 个行星成功。
- 自动循环：两次独立测试各完成 2 次跃迁和 2 次整系扫描；最近一次还验证了 F1 暂停约 13.5 秒后继续。相关摘要在 `research/automatic-trial-20260903T1049.json`、`research/two-cycle-trial-20260903T1145.json` 和 `research/two-cycle-controls-trial-20260903T1153.json`。
- 自动上传：F2 在启动前开启，自动跃迁后扫描 5 个行星；约 2.016 秒后游戏原生“上传全部”处理 16 条待上传记录，并按一轮上限正常完成。用户确认游戏中上传成功；证据见 `research/upload-trial-20260904T0100.json`。
- 最终验收：用户确认 F1、F2、F3 及完整自动流程均正常；有效会话汇总见 `research/acceptance-20260904.json`。
- 62 项非游戏测试、Ruff、编译检查和当前 EXE 的 16 个原生入口核查通过。

最近测试的所有关键动作事件都记录为游戏在前台，所以上述结果不能当作后台完整流程的客观验收。程序不抢输入，失焦后游戏更新函数仍会运行；失焦循环和最小化仍需分别实测。自动跃迁、整系扫描、F1 暂停/继续和可选上传均已在游戏中验证。

## 安全检查和日志

每次启动先核对 NMS.exe 的 SHA-256、映像大小、x64 格式、16 个入口的唯一机器码签名，以及主游戏对象的两处引用。注入前还会检查固定本地执行端口是否被旧会话占用，避免留下半加载运行时。版本变化、签名歧义、目标或状态不一致、错误线程、错误货船状态、地图未稳定、发现记录类型异常等情况都会停止，不会盲目继续。

日志写入 `logs/native-observer.jsonl`，框架日志写入 `logs/pymhf-*.log`，启动前错误写入 `logs/launcher-errors.jsonl`。不记录恒星坐标、传送门符文、对象地址或发现名称。

自动上传会验证：APPVIEW 状态、刚加载的目标恒星系、自己的货船、游戏未暂停、没有跃迁或界面切换请求、真实前端对象，以及每条待上传记录的类型范围。它调用游戏嵌入的真实发现页数据，不伪造 UI 对象；处理后只有游戏清空待上传集合才算本地接收成功，否则停止循环。

## 运行限制

`native/exploration.json` 中：

| 配置项 | 默认值 | 含义 |
|---|---:|---|
| `max_warps` | 0 | 最大跃迁次数；0 为不限 |
| `max_runtime_seconds` | 0 | 从首次 F1 起的最长秒数；0 为不限 |
| `cycle_interval_seconds` | 5 | 首次和每轮之间的等待 |

发行包 EXE 接受 `--max-warps 数量` 和 `--max-runtime-seconds 秒数`。例如在命令行运行 `NoMansSkyScanner.exe --max-warps 10`。源码中的 `start_two_cycle_test.bat` 固定为两轮/180 秒；`start_upload_test.bat` 固定为一轮/180 秒，用于首次 F2 上传验证。

## 源码环境

要求 Windows 11、Python 3.12 x64。依赖精确版本见 `requirements.lock.txt`，来源为官方 PyPI：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple -r requirements.lock.txt
.\.venv\Scripts\python.exe -X utf8 -m nms_scanner.launcher --loop
```

离线核查，不连接游戏：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m nms_scanner.launcher --loop --exe "游戏目录\Binaries\NMS.exe"
```

程序只进行普通银河地图常规跃迁，不选择银河中心目标，不使用黑洞、Relic Gate 或银河切换。它不筛选行星、不命名发现、不战斗或补充资源，也没有安装 Meta Mod。常规跃迁、扫描和用户开启的上传会触发游戏自身正常的资源、奖励、发现和保存行为。

## 仓库内容与许可

仓库保存源代码、配置、测试、构建脚本和经过整理的验证证据。游戏日志、虚拟环境、发行包、外部研究仓库和原始反汇编文本不会提交。可直接运行的便携发行包由 `tools/build_release.py` 在本机构建。

本项目按仓库根目录 `LICENSE` 中的 GNU Affero General Public License v3.0 授权。
