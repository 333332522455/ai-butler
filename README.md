# AI 管家（ai-butler）

把 DeepSeek Harness（DSH）变成一台"懂你、能动手"的 Windows 桌面助手的一组可分发工具。

本仓库只放**可分发、无个人数据**的东西。个人记忆库、课表、日志、会话、凭据全部留在私有仓库
`sleep` 里，不进这里。

## 现在的样子

| 组件 | 文件 | 作用 |
| --- | --- | --- |
| 全局呼出条 | `tools/hotkey.py` | `Ctrl+Alt+Space` 弹出输入条，回车把话送进 DSH 会话；调出时**自动截当前活动窗口**一起发过去 |
| 截屏守卫 | `tools/window_shot.py` | 只截前台窗口（不是整屏），命中黑名单（凭据/支付/聊天/邮箱/私密/登录）只发文字、连标题都不外发；截图保留 3 天 |
| 操控气泡 | `tools/agent-bubble.py` | 右下角小气泡：AI 正在操作你的鼠标键盘时琥珀色提示"正在操控屏幕"，需要你接手时变红 |
| 桌面操作 | `tools/desktop.py` | 真点屏幕：观察 → 判定 → 点击/打字/按键 → 复验的四步闭环，带窗口白名单 |
| 统一执行器 | `tools/dev.py` + `dev.cmd` | 每条命令落盘独立日志，假成功有独立证据可查 |
| 事件总线 | `tools/event-bus.py` | 三类事件（文件落地/服务掉线/定时到点）+ 纯代码初筛（白名单、夜间只放 P0、5 分钟合并），不花 token |
| 离线激活码 | `tools/license.py` | 机器码 → 作者算码 → 本地 HMAC 校验；`status/code/check/make/activate/--unbind` |
| 任务书 | `docs/AI管家开发任务书-v3.1-Windows实况版.md` | 整体设计与三步落地路线（含 rollover、部署、授权） |

## 平台与前提

- **Windows 10/11**（重度依赖 Win32：DPI 感知、RegisterHotKey、窗口枚举、剪贴板）
- Python 3.11+（只用标准库 + Pillow）
- 宿主：DeepSeek Harness（本机 `http://127.0.0.1:43120`）

## 安装（一条命令）

先看再跑（推荐）：

```powershell
irm <你的短链>/i -OutFile install.ps1
notepad install.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

装了不用也能先体检：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -DryRun
```

`install.ps1` 做八件事：探测 Python（3.9+，含 pythonw）/ 检查 Pillow / 校验安装源 /
检测热键是否被占用 / 发现已在运行的旧实例 / 复制文件到安装目录并生成启动器 /
注册登录自启计划任务（`wscript //B` 包 vbs，全程无黑框）/ 立即启动。

参数：`-InstallDir`（默认 `%LOCALAPPDATA%\ai-butler`）、`-PythonPath`、`-Hotkey`、
`-InboxUrl`、`-TaskName`、`-NoAutostart`、`-NoStart`、`-DryRun`、`-Force`（结束旧实例）。

卸载：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1            # 停进程 + 注销任务 + 删目录
powershell ... -File .\uninstall.ps1 -KeepFiles                              # 只停进程/注销任务，留文件
```

## 数据放在哪

不写死任何绝对路径：数据目录 = 安装目录（`tools/` 的上一级），可用环境变量覆盖。

| 变量 | 作用 | 默认 |
| --- | --- | --- |
| `AI_BUTLER_HOME` | 日志/截图/状态文件的根 | `tools/` 的上一级 |
| `AI_BUTLER_HOTKEY` | 热键写法，如 `ctrl+shift+f9` | `ctrl+alt+space` |
| `AI_BUTLER_INBOX` | 投递地址 | `http://127.0.0.1:43120/wechat-bridge/inbox` |

产物：`<AI_BUTLER_HOME>/logs/hotkey.log`、`logs/bubble.log`、`logs/agent-state.json`、
`logs/shots/`（截图，保留 3 天）。

## 快速跑一个（不安装）

```bat
py tools\hotkey.py          REM 全局呼出条
py tools\agent-bubble.py    REM 操控气泡（需要 Pillow）
py tools\window_shot.py --check   REM 看前台窗口和黑名单判定
```

## 已知限制（正在做）

- 常驻方式仍是"wscript 包 vbs 后台跑 pythonw"，没做成托盘程序。
- 安装器只覆盖 Windows；没有开机自启以外的自愈（进程挂了不会自动重启）。
- 与 DSH 的耦合点只有一处：向 `/wechat-bridge/inbox` POST `{"text": ...}`，换宿主时改这里即可。

## 黑名单

`tools/shot-blocklist.json` 是截图黑名单（分类 + 可执行文件名 + 标题关键词）。
默认挡：密码管理、网银支付、聊天、邮箱、私密、登录页。宁可少截，不可多传。

## 许可

MIT，见 `LICENSE`。
