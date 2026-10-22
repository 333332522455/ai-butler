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

```powershell
irm https://333332522455.github.io/ai-butler/bootstrap.ps1 | iex
```

`bootstrap.ps1` 只做三件事：从 codeload 下载本仓 zip → 解到 `%TEMP%` → 调用包里的 `install.ps1`，跑完自动清理临时目录。

下载过程有动画：ASCII banner、`#>` 进度条（百分比 / MB / KB每秒 / ETA）、连接阶段带秒数的 spinner；连接 25 秒超时、读超时 15 秒，失败自动重试 3 次，最终失败会带明确原因退出（退出码 1）。重定向/CI 环境自动关动画（`AI_BUTLER_ANIM=1` 可强制打开）。

想先看再跑：

```powershell
irm https://333332522455.github.io/ai-butler/bootstrap.ps1 -OutFile boot.ps1
notepad boot.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\boot.ps1
```

装了不用也能先体检（不落盘）：

```powershell
$env:AI_BUTLER_INSTALL_ARGS='-DryRun'; irm https://333332522455.github.io/ai-butler/bootstrap.ps1 | iex
```

环境变量：`AI_BUTLER_INSTALL_ARGS`（转给 install.ps1 的参数，如 `-NoAutostart`）、
`AI_BUTLER_ZIP_URL`（换包源，镜像/离线）、`AI_BUTLER_KEEP_TEMP=1`（保留解包目录）、
`AI_BUTLER_PLAIN=1`（关动画）、`AI_BUTLER_ANIM=1`（强制开动画）。

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

## 临时用一下：零安装聊天客户端（不用装 DSH）

换电脑、借朋友机器、只想问两句的时候，用这一个脚本就够，不需要 Python、不需要安装：

```powershell
irm https://333332522455.github.io/ai-butler/chat.ps1 | iex
```

也可以只下一次、看清楚了再跑：

```powershell
irm https://333332522455.github.io/ai-butler/chat.ps1 -OutFile chat.ps1
notepad chat.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\chat.ps1
```

非交互（适合管道和测试）：

```powershell
powershell -File .\chat.ps1 -Once "这段报错怎么改：0xC0000005"
```

能力：流式逐字输出、多轮上下文（自动限长）、`/file <路径>` 把文件带进对话（文本，2 MB 上限、超长截断）、`/save` 存 markdown、`/model` 换模型、`/cost` 看本次 token、`/key` 换 key、`/help` 看全部。

### 保密管理（重要）

- **它不含任何密钥**。仓库里只有一个空壳脚本，谁拿到都得自己填 key。
- key 只存在你这台机器的 `%APPDATA%\minichat\key.txt`（UTF-8 无 BOM，仅当前用户可读），
  或临时用 `-key` / 环境变量 `MINICHAT_API_KEY` 传入。
- 界面上 key 只显示成 `sk-a...9f2c` 这种掩码，**任何日志、报错、`/save` 导出里都不会出现完整 key**。
- 想彻底不留痕：不用 `/key`，每次用环境变量传，关掉窗口就没了。
- 默认模型 `deepseek-chat`，可 `-model` 或 `/model` 换；默认地址 `https://api.deepseek.com/v1`，可 `-base` 换成任何 OpenAI 兼容端点（本地 8090 代理、自建网关都行）。


## 像 Claude Code 一样用：本地工作区 agent（agent.ps1）

chat.ps1 只能聊；`agent.ps1` 能自己看文件、跑命令、读报错、改完再自测。同样零依赖、纯 PowerShell。

```powershell
irm https://333332522455.github.io/ai-butler/agent.ps1 -OutFile agent.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\agent.ps1
```

第一次运行会**在桌面建一个 `ai-butler-workspace` 文件夹**当工作区（`install.ps1` 安装时也会顺手建好并把 agent.ps1 放进去）。

- 工作区：默认 `桌面\ai-butler-workspace`，可用 `-ws <目录>` 或环境变量 `AGENT_WORKSPACE` 换。
- 四个工具：`list_dir` / `read_file` / `grep` / `run_command`。**所有路径都被围栏在work区里**，`..\..\win.ini` 这类越界请求会被直接拒绝。
- 审批：只有 `run_command` 会问一次，`[y]es` 单次放行、`[n]o` 拒绝、`[a]ll` 本次会话之后全放行；启动时带 `-yes` 等于全自动（自己看着办）。
- 收尾小测试：每轮任务结束时自动跑一条小命令自检，打印 `post-task selftest: ok`，失败返回码 2。
- 命令：`/help` `/ws` `/tools` `/clear` `/exit`；行首 `!命令` 直接在本地跑，不走模型。
- 非交互：`powershell -File .\agent.ps1 -Once "看看这里有什么文件"`。

`-selftest` 可单独验证四个工具与越界拦截（6 项检查）。

安全提醒：`run_command` 用的是你当前账户权限，删文件/装东西都做得到。不确定的仓库先 `-OutFile` 下来看一遍再跑。

## 已知限制（正在做）

- 常驻方式仍是"wscript 包 vbs 后台跑 pythonw"，没做成托盘程序。
- 安装器只覆盖 Windows；没有开机自启以外的自愈（进程挂了不会自动重启）。
- 与 DSH 的耦合点只有一处：向 `/wechat-bridge/inbox` POST `{"text": ...}`，换宿主时改这里即可。

## 黑名单

`tools/shot-blocklist.json` 是截图黑名单（分类 + 可执行文件名 + 标题关键词）。
默认挡：密码管理、网银支付、聊天、邮箱、私密、登录页。宁可少截，不可多传。

## 许可

MIT，见 `LICENSE`。
