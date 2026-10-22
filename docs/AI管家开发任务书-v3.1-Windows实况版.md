# 本地 AI 管家（DeepSeek Harness 魔改版）开发任务书

> 版本：**v3.1 · Windows 实况版**
> 基准：v3.0（2026-10-22，Linux 蓝图）
> 更新：2026-10-22
> 引擎：DeepSeek V4 Flash API（走本机路由代理 8090）
> 运行环境：**Windows 11 + DSH Desktop 2.0.17（Electron）**

---

## 一、先定位：这不是从零造，是补两块

v3.0 把目标写成"把 DSH 从终端 CLI 工具改造成常驻管家"。这个前提不成立——**本机已经是一个常驻、带记忆、带定时、带自动验证的管家**。所以本期任务不是造新的，是**补上缺的两块，并把已经有的写清楚、别重复投资**。

### 现状盘点（2026-10-22 实测）

| 能力 | 现状 | 对应 v3.0 |
|---|---|---|
| 后台常驻 | ✅ 8 个常驻服务（路由代理 8090 / QQ 桥 43124 / 会话看门狗 / 主动通知 / 备份守护 / 微信监测 / 上课提醒 / 后台补发） | Phase 0 |
| 桌面通知 | ✅ QQ 桥推送 + serverchan 回执 | Phase 0 |
| 代码自动验证 | ✅ `handoff.py verify`（退出码语义）+ `--review` 自审 | Phase 2 / 7 |
| 改完自动提交 | ⚠️ 半有（gh 可用，未自动 commit） | Phase 2 |
| 用户偏好持久化 | ✅ memory-eternal（SQLite）+ BOOTSTRAP.md + journal.jsonl | Phase 3 |
| 历史检索 | ✅ `memory_recall`、`journal --find` | Phase 3 |
| 上下文水位监控 | ✅ watch-notify 每 60 秒看会话文件，分档注入 CTX-GUARD | Phase 3 |
| 定时任务 | ✅ schedule 插件 + 每晚 digest + 上课提醒 | Phase 4 |
| 会话自动浓缩 | ✅ digest（已归档 / 上下文爆 / 闲置 6h 三触发） | Phase 3 |
| 进程重启 | ✅ restart-dsh.py（计划任务派生，脱离进程树） | — |
| **全局呼出** | ❌ **没有** | Phase 0 |
| **事件总线 + 主动判断** | ❌ **没有** | Phase 6 |
| **自动接棒新会话** | ❌ 没有（但官方 API 存在，见第五节） | Phase 3 |

**结论：v3.0 的 8 个 Phase 里，5 个已经在跑，1 个半有，真正缺的只有三件。**

---

## 二、平台事实（v3.0 全篇的 Linux 假设都要换掉）

| v3.0 写的 | Windows 本机对应 | 备注 |
|---|---|---|
| systemd 服务 | 计划任务 + `wscript //B` 包 `.vbs` | 直接指向 .cmd 会弹黑框（已有教训） |
| inotifywait | Python `watchdog`（推荐）或 `ReadDirectoryChangesW` | 不要用轮询，CPU 白烧 |
| xdotool + Ctrl+Alt+Space | Win32 `RegisterHotKey` | Python `ctypes` 可直接调，无需第三方 |
| notify-send | QQ 桥推送 / `BurntToast`（PS 模块） | 现成通道优先 |
| xprop / wmctrl | `GetForegroundWindow` + `GetWindowText` | `tools/desktop.py windows` 已实现 |
| `install.sh \| bash` | `install.ps1` / `install.cmd` | 仓库 333332522455/sleep |

**编码三条硬规矩（本机踩过）**
1. `.cmd/.bat` 一律**纯 ASCII + CRLF**——控制台代码页是 65001，GBK 中文路径必挂；
2. `.ps1` 含中文必须**UTF-8 带 BOM**，否则 PS 5.1 解析失败、计划任务静默 0x1；
3. 调 Python 一律显式 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`。

**解释器绝对路径**（不在 PATH 上，别猜）
- Python：`D:\新建文件夹 (2)\python.exe`（另有 `pythonw.exe` 用于无窗口）
- Node：`D:\sleep\tools\node22\node.exe`
- 统一执行入口：`D:\sleep\dev.cmd`（run / py / node / status / verify / log）

---

## 三、本期要做的三件事

### 第 1 步：全局呼出（3 天，收益最大）

**目标**：任意时刻按 `Ctrl+Alt+Space`，弹出输入框，说完直接发给当前会话。

**实现**
1. `hotkey.py`：`ctypes` 调 `RegisterHotKey` + 一个无边框 Toplevel（Tkinter 即可，不引入 Qt）；
2. 提交时 POST 到 `http://127.0.0.1:43120/wechat-bridge/inbox`（现有桥接口，202 已实测），走 `followup` 模式排队，不打断当前回合；
3. 常驻用计划任务拉起（`wscript //B` 包一层，`-Hidden`）。

**验收**
- 按热键 1 秒内出现输入框；
- 输入文字回车后，HTTP 返回 202 且会话内出现该消息；
- 隐藏窗口后台常驻，任务管理器里无黑框。

### 第 2 步：事件总线 + 规则初筛（1–2 周）

**目标**：把"值得说的事"筛出来，其余 90% 直接丢弃，不花一分钱。

**只接 3 类事件**（先把范围钉死，别贪）
1. **文件落地**：`D:\dsh-home\attachments`、`D:\sleep\大一信息台`、`D:\sleep\jiuhu` 三个目录新增/修改；
2. **服务与端口**：43120 / 43124 / 8090 掉线，或常驻进程数下降；
3. **定时**：schedule 触发 + 每日 09:00 晨报。

**规则初筛（纯代码，零 API）**
- 已知扩展名白名单、单文件 < 100KB 直接忽略；
- 5 分钟内同类事件合并为一条；
- 端口抖动 30 秒内恢复则不上报；
- 夜间 23:30–07:00 只允许 P0（服务挂了）通过。

**验收**
- 连续 24 小时运行，事件日志里"进入 AI 判断"的条数 < 总数的 10%；
- 触发一条真实事件（往监听目录丢文件），日志能看到"规则放行 → 待判断"。

**落地状态（2026-10-22）**：`tools/event-bus.py` 已实现并常驻。运行参数 `--once / --loop --interval 15 / --report / --dry-run / --now ISO / --base DIR`（默认 base = `AI_BUTLER_HOME` 或 `D:\ai-butler`）；日志 `logs/events.jsonl`（at/id/kind/target/priority/verdict/reason，merge 行带 merged_to），状态 `logs/event-bus-state.json`。三类事件 + 扩展名白名单 / <100KB 丢弃 / 夜间只放 P0 / 端口 30 秒抖动不上报 / 5 分钟同类合并 全部在代码里；`detect_service()` 可被测试替换。验证脚本 `D:\sleep\logs\_verify_eventbus.py` 7 项全 PASS（已入 `D:\sleep\logs\verify.jsonl`）；真实链路测试：往 `D:\dsh-home\attachments` 丢一个 220KB PDF → 25 秒内 events.jsonl 出现 `queue/P1`。启动器 `D:\sleep\tools\start-eventbus-hidden.vbs`，已接入 `start-all.ps1` 第 [16] 项。**未做**：每日 09:00 晨报（属第 3 步的输出形态）。

### 第 3 步：主动判断 + 打扰控制（1 周）

**目标**：该说话时说话，不该说时闭嘴。**没有这三条闸，主动引擎会变成骚扰器。**

1. **冷却期**：同一类事件 20 分钟内最多打扰 1 次；
2. **专注模式**：检测前台窗口标题命中（Dev-C++ / Keil / 浏览器全屏），只记不说；
3. **日预算**：当天 AI 调用成本超过阈值即刻闭嘴，只留 P0 通道。

**验收**
- 人为制造 10 次相同事件，用户只收到 ≤1 条；
- 预算触顶后，非 P0 事件不再调用模型（日志可证）；
- 全天打扰次数可统计（要有个 `--report` 能出日报）。

---

## 四、会话自动接棒（rollover）

### 4.1 事实更正
v3.0 Phase 3 写"上下文 80% → 自动总结 → 开新对话无缝接上"，我最初评审判断"DSH 没有对外切换会话的 API，做不到"——**这条判断错了**。

官方 API 就在本地代码里：
- `node_modules\@deepseek-ai\dsh-api-session-controller\lib\index.js:686` → `async create(request)`，参数 `{sessionId?, workspaceId? | cwd?, agentPreset?}`
- 同文件 `:776` → `async fork(request)`，参数 `{sessionId, atSeq?}`，注释明确："an explicit atSeq is the inclusive cut; an omitted value selects the latest completed-turn prefix"

调用通道是 Typert RPC（`dsh-api-gateway`，挂在 HTTP `/api` 的 FetchHandler 上），**不是普通 REST**，需要走 RPC 编码或在 Cordis 插件里用 `ctx.remote`。

### 4.2 关键陷阱
`fork` 复制的是**事件前缀**，不做任何压缩。直接 fork 最后一轮，新会话跟旧会话一样满，等于白换。
所以必须拆成三段，每段独立可测：

| 段 | 做法 | 难点 |
|---|---|---|
| ① 换会话 | `session.create` 建空会话（推荐）或 `fork(atSeq=早期切点)` | 低 |
| ② 搬上下文 | 拼"接棒消息"：BOOTSTRAP 摘要 + 旧会话 digest + 最后 3–5 轮原文 | **中，决定体验** |
| ③ 改绑定 | 提醒（走 schedule API，**禁止手改 json**，T25 教训）、桥 `sessionId`、watch-reply 进程、旧会话归档 | 中 |

### 4.3 两条落地路线

**A. 外部脚本（推荐先做，约 3 天）**
`rollover.py`：watch-notify 水位到 80% 触发 → create → 注入接棒消息 → 改绑定 → 归档。
已知不完美：GUI 里"当前选中会话"是前端本地状态，脚本换会话后**需要手动点一下新会话**。

**B. Cordis 插件（真无感，约 1 周）**
`ctx-rollover`：Host 侧调 API + 配一个 client 插件让界面自动切过去。代价是要碰内部 API，DSH 升级可能失效（跟现有 vendor 补丁一样需维护）。

**决策**：先 A。A 跑顺了，B 只是把同一逻辑搬进插件；而且 A 能先把真正的难点——"接棒消息写得够不够"——验证掉。

---

## 五、明确不做 / 押后

| 项 | 理由 |
|---|---|
| Phase 1 文件自动整理/去重/重命名 | 看着酷，收益低于风险；误移误删一次就够受 |
| Phase 5 窗口焦点感知 | 先只做"专注模式"这一个用途（第 3 步已含），完整的活动统计押后 |
| L5 键鼠模拟 | 不可回滚，出事没法解释 |
| L6 内核级 | 定义改为：**不碰内核驱动与系统服务**；管理本项目进程（重启器）不算越界 |
| 历史对话向量检索 | 现有 memory_recall + journal 检索已够用，向量库先把接口留空 |

---

## 六、成本与预算（v3.0 估算偏低）

**实测基线**：2026-10-21 单日消耗 ¥10.21；路由代理记账显示当日 14 次请求 / 输入 74,022 tok / 输出 13,116 tok。

v3.0 写"40–80 元/月"，若主动引擎每 5–10 分钟调一次模型判断，**轻松破 300**。因此：

| 闸门 | 设置 |
|---|---|
| 日预算硬上限 | **3 元**（超了只留 P0） |
| 单次判断 token 上限 | 输入 ≤ 4K，输出 ≤ 300 |
| 判断频率 | 事件驱动，不做固定轮询 |
| 夜间 | 23:30–07:00 不调模型（P0 除外） |

预算统计直接读 `D:\sleep\llm\proxy\usage.jsonl`（已有记账）。

---

## 七、里程碑与验收

| 里程碑 | 周期 | 验收（必须可测） |
|---|---|---|
| M1 全局呼出 | 3 天 | 热键 1 秒出框，回车后会话收到，HTTP 202 |
| M2 事件总线 + 初筛 | 1–2 周 | 24h 内 AI 判断调用 < 10% 事件量 |
| M3 主动判断 + 打扰控制 | 1 周 | 10 次同类事件只扰 1 次；预算触顶闭嘴可证 |
| M4 rollover-A | 3 天 | 水位 80% 自动换会话，接棒消息正确，绑定全部指向新会话 |

每个里程碑完成都要跑一次验证并写入 `logs/verify.jsonl`（项目内硬规矩）。

---

## 八、风险清单

1. **DSH 升级会覆盖 vendor 补丁** → 升级后必须重跑 `tools/fix-dsh-vendor.cjs`，并复测 rollover 与热键；
2. **手改 `D:\dsh-home\storages\schedule.json` 会触发 ZodError 并让整个调度域静默失效**（T25）→ 一律走 API；
3. **计划任务 Action 直接指 .cmd 会弹黑框** → 必须 `wscript //B` 包一层；
4. **成本失控** → 日预算硬闸 + 夜间静默，先上闸再做功能；
5. **主动引擎误报** → 首周只上报不自动执行任何写操作。

---

## 九、一键部署与授权（Windows）

### 9.1 分两层，别混在一起

| 层 | 内容 | 部署方式 |
|---|---|---|
| L1 DSH 本体 | Electron 应用 + 官方插件 | **手动装官方安装包**，不归本项目管 |
| L2 本项目 | 工具脚本、插件、常驻服务、全局热键 | 一条命令 |

### 9.2 一条命令（目标 ≤ 30 字符）

```powershell
irm dsh.example.com/i | iex
```

实现：便宜域名 → 302 跳到 GitHub raw 的 `install.ps1`；或直接用 GitHub Pages（零成本）：
```powershell
irm <user>.github.io/i | iex
```

**同时必须提供「先看再跑」版本**（README 里给）：
```powershell
irm dsh.example.com/i -OutFile install.ps1; notepad install.ps1; .\install.ps1
```
`irm | iex` 是不看内容直接执行的经典反模式，懂行的人会因此不敢用。

### 9.3 install.ps1 必须做到的六件事

1. **探测而非硬编码**：找 python / node / DSH 安装目录 / 目标安装路径（本机是 `D:\sleep` + `D:\新建文件夹 (2)`，换机全不同）；
2. **幂等**：重复执行不报错、不重复注册任务；
3. **注册计划任务**：Action 必须 `wscript //B` 包 `.vbs`（直接指 .cmd 会弹黑框）；
4. **热键冲突检测**：已被占用就提示换键，别静默失败；
5. **写配置模板**：首次运行向导生成，不打包个人数据；
6. **配套 uninstall.ps1**：删任务、删热键、清配置，不留残渣。

**落地状态（2026-10-22）**：六件事已全部落地，其中第 5 件由 `tools/wizard.py` 承担。

- `tools/abconfig.py`：配置唯一读入口。优先级 环境变量 > `<BASE_DIR>\config.json` > 内置默认；`BASE_DIR` = `AI_BUTLER_HOME` 或 `tools/` 的上一级，开发树与安装目录同一份代码都成立。只认 `hotkey / inbox / shot / installDir / taskName` 这几项，**不放任何个人数据**（对话、记忆、课表、密钥都不在这里）。
- `tools/wizard.py`：首次运行向导。交互模式逐项问（热键、投递地址、截图开关、授权码），`--yes` 全默认非交互（`install.ps1` 用这个），`--show` 只读回显，`--hotkey/--inbox/--shot on|off` 直接指定。热键真注册一次探测冲突（`RegisterHotKey` + `use_last_error=True` 判 1409 = 已被占用），非法输入退 2；合法就写 `config.json` 并保留安装器写的 `python/pythonw` 字段。
- `hotkey.py` 改读配置：`HOTKEY_SPEC = CFG.get("hotkey")`、`INBOX_URL = CFG.get("inbox")`、`SHOT_ENABLED = bool(CFG.get("shot", True))`；`--no-shot` 仍可临时覆盖。日志会打实际生效的热键（`hotkey ready: ctrl+alt+space`），可直接看出配置有没有被读到。
- `install.ps1`：`$need` 加 `wizard.py`/`abconfig.py`（现 9 个文件），`config.json` 增加 `shot = $true`，装完自动跑一次 `wizard.py --yes --no-license` 把配置规范化（热键写错在安装阶段就暴露），摘要新增「改配置」一行。
- 验证脚本 `D:\sleep\logs\_verify_wizard.py` **26 项全 PASS**（默认值、规范化 ALT+F9→alt+f9、截图写 false、wizardAt、无个人数据字段、`--show`、非法热键/地址退 2、环境变量覆盖、重跑保留 python 字段、hotkey 三处接线、安装器接线与 BOM、py_compile），已入 `D:\sleep\logs\verify.jsonl`。

### 9.4 仓库拆分（隐私硬要求）

`D:\sleep` 里有 journal、记忆库、课表、作业、会话摘要，**绝不能推公开仓库**。

| 仓库 | 可见性 | 内容 |
|---|---|---|
| `sleep` | **私有** | 个人数据 + 记忆 + 日志 + 全部项目 |
| `ai-butler` | 可公开 | 只放可分发工具：dev.py、handoff.py、hotkey、气泡、install/uninstall.ps1、配置模板 |

`.gitignore` 必须挡住：`logs/`、`*.jsonl`、`记忆*`、`sessions/`、`*.db`、`.state/`。

### 9.5 授权与加密（三档，选档 2）

**前提 honesty：纯客户端加密防不住逆向。** 代码要在别人机器上跑，密钥必然在本地。加密防的是「顺手复制源码的人」。

| 档 | 做法 | 防谁 | 代价 |
|---|---|---|---|
| 1 | Nuitka 编译 exe / PyArmor 混淆 | 防直接读源码 | 免费；exe 20–60MB；启动慢 1–2s；杀软可能误报 |
| 2 ✅ | **离线激活码**：首启显示机器码，作者算出激活码；程序 HMAC 本地校验 | 防「随便谁都能用」 | 免费；需人工发码；换机要重发 |
| 3 | 在线授权：启动联网换令牌 | 真正可控、可吊销 | 需服务器 + 域名 + 维护 |

**档 2 实现要点**
- 机器码 = `SHA256(主板序列号 + C 盘卷序列号)` 前 16 位，只算不传；
- 激活码 = `HMAC_SHA256(密钥, 机器码)[:16]`，大写分组显示（XXXX-XXXX-XXXX-XXXX）；
- 本地存 `%APPDATA%\ai-butler\license.json`，含激活码 + 激活时间；
- 提供 `--unbind` 解绑（删 license.json），用于换机；
- **密钥不进仓库**：作者本地保存，编译时注入。

**落地状态（2026-10-22）**：`tools/license.py` 已实现（纯标准库）。机器码 = `SHA256(MachineGuid + C 盘卷序列号)`[:16] 大写；激活码 = `HMAC_SHA256(密钥, 机器码)`[:16]，显示为 `XXXX-XXXX-XXXX-XXXX`；license 存 `%APPDATA%\ai-butler\license.json`（machine / code / activatedAt / note）。密钥查找顺序：`AI_BUTLER_LICENSE_KEY` 环境变量 → `%APPDATA%\ai-butler\author.key` → `<BASE_DIR>\author.key`（都不进仓库）。子命令 `status / code / check`（0 已激活、3 未激活）、`make --machine`、`activate CODE`、`--unbind`。**已接闸**：`hotkey.py` 启动时未激活直接退出（退出码 4，日志记原因），开发机可用 `AI_BUTLER_DEV=1` 绕过；`install.ps1` 装完自动打印机器码与授权状态，并提示「把机器码发给作者换码 → license.py activate」。验证脚本 `D:\sleep\logs\_verify_license.py` **15 项全 PASS**（算码格式、错码拒绝、篡改机器码/激活码均判未激活、换密钥失败、unbind、DEV 绕过、py_compile），已入 `D:\sleep\logs\verify.jsonl`。本机已用自己的密钥激活（机器码 E681-C07E-5892-F6AB）。

**副作用（接受）**：作者自己改代码要重编译；换机要重发码。

### 9.6 但真正值钱的不是代码

分发的只是几个工具脚本。让「管家」成立的是记忆库、BOOTSTRAP、课表、偏好、桥配置——**这些不在公开仓库里**。别人装出来是个空壳。

因此加密投入到此为止：再往上加钱加时间，换来的安全提升很有限。

---

## 十、附录：v3.0 逐条勘误

| v3.0 原文 | 问题 | v3.1 处理 |
|---|---|---|
| 运行环境：Linux | 与实机不符，全篇工具链不可用 | 换成 Windows 对应表（第二节） |
| Phase 0 从零做 systemd | 已有 8 个常驻服务 | 只补"全局呼出" |
| Phase 2 自动测试闭环 | 已实现 | 标为"已有"，只补"自动 commit" |
| Phase 3 无感开新对话 | 原判"做不到" | **更正：有 API**，见第四节 |
| Phase 6 事件驱动 | 方向对，但缺"先上闸"顺序 | 预算/冷却/专注三条并入第 3 步验收 |
| L6 别碰进程 | 与 restart-dsh.py 冲突 | 改为"不碰内核驱动与系统服务" |
| 成本 40–80 元/月 | 估低 | 改日预算 3 元硬上限 |
| 总周期 11–14 周 | 大部分已做完 | 压到 3–4 周拿 90% 价值 |
