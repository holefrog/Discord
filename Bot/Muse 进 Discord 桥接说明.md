# Muse 进 Discord：桥接技术文档

> 现行版本对应 2026-11-13 定案的「VPS 本地轮询」架构。旧架构（Muse 侧轮询）仍在线上运行，切换完成后本文件即为唯一实现说明；切换前的历史见 [项目需求（归档）](bot-muse-requirements.md)。

## 1. 总体结构

```
Discord ⇄REST⇄ [VPS] poll_vps.py（常驻轮询） → 队列文件（inbox.jsonl）
                                                        ⇅
                              [VPS] Web 服务 /api/bridge/*（队列小口）
                                                        ⇅ REST（5 秒查一次）
                                                 [Muse] hook 查信箱 → 唤醒 worker → 取件 → 组织回复 → 递回小口发送 → 销账
```

- Discord 没有给普通机器人用的官方托管接入，桥接为自建。
- 机器人：Discord 应用 `App-Bot-Muse` 下的 bot 账号 **Bot-Muse**（user id 只存部署侧 config.json，不入仓）。
- 全程 REST，不用 Gateway 长连接（2026-11-13 定案；理由见 README「关键设计决定」）。

## 2. VPS 侧：轮询程序 `poll_vps.py`

纯 Python 标准库，单文件。常驻运行由 systemd 托管（崩溃自动拉起）。

### 2.1 一圈的动作

1. **发现话题帖**：按服务器枚举活跃 thread（`GET /guilds/{guild_id}/threads/active`，Discord 没有频道级活跃 thread 接口）；再按父频道补枚举归档 thread。新发现的 thread 首轮回扫近 2 小时 @，防止漏掉更早的提问。
2. **取新消息**：对每个频道/thread 调 `GET /channels/{id}/messages?limit=50&after=<水位>`。Discord 消息 id 按时间递增，`after` 只返回水位之后的新帖，老帖不重复传输。
3. **过筛**：跳过 bot 自己的消息；其余按 @ 判定——消息 `mentions` 含 bot user id 或历史遗留旧 bot id，或 `mention_roles` 含被监听的身份组 id（见 §5），才算点名。
4. **入队**：命中的消息写 `inbox.jsonl`，字段含正文、作者、频道、附件链接，并顺带拉取该消息之前 25 条（`before=<id>&limit=25`）格式化为上下文一并存入，Muse 侧取件后无需再查 Discord。
5. **落盘顺序**：先写队列，再推进并保存水位（`watermarks.json`），最后逐条发「收到，正在处理」并记 `acked.json`。任何一步崩溃，重启后靠队列去重（消息 id 比对）不重复入队。
6. 父频道首次运行只设水位、不回放历史。

单圈约 25 次请求；VPS 到 Discord 单次请求实测 0.17–0.25 秒，整圈约 **5–8 秒**。请求串行执行；遇 Discord 429 按返回的 `retry_after` 等待，不重试硬闯。

### 2.2 状态文件（VPS `~/discord-bridge/`，目录 700）

| 文件 | 内容 |
|---|---|
| `config.json` | 频道表、身份组 id、旧 bot id（见 §5） |
| `token.txt` | bot token 整行（`Bot <token>`），600 权限，所有者 亲手送达 |
| `hook-token` | Muse↔VPS 小口的独立钥匙，600 权限 |
| `watermarks.json` | 各频道/thread 的水位（最后处理的消息 id） |
| `inbox.jsonl` | 待回复队列（含上下文） |
| `acked.json` | 已发「收到」的消息账本 |
| `replied.json` | 已回复账本（小口销账时写） |

### 2.3 鉴权细节（血泪教训，勿改）

- token 文件存**整行** `Bot <token>`，代码原样放进 `Authorization` 头，**禁止**在代码里拼 `"Bot " + token`：代理/服务端按精确值匹配，拼接曾导致有效 token 连吃 401。
- 所有请求必须带真实 `User-Agent`（如 `DiscordBot (bot-muse-bridge, 1.0)`），否则 Cloudflare 直接 403（error 1010）。

## 3. 队列小口（VPS Web 服务，评分卡同机 :3000）

挂在现有 Web 服务上，路径前缀 `/api/bridge/`，每个请求须带请求头 `X-Bridge-Token`（与 `hook-token` 文件一致，恒定时间比对；无钥匙 401）。

| 接口 | 方法 | 作用 |
|---|---|---|
| `/api/bridge/pending` | GET | 返回待回复条数（队列减已回复账本） |
| `/api/bridge/inbox` | GET | 返回全部待回复条目（含上下文） |
| `/api/bridge/reply` | POST | body `{channel_id, content}`；VPS 用 bot 身份发进频道，自动按 1900 字符分段，返回消息 id 列表 |
| `/api/bridge/replied` | POST | body `{id, reply_id}`；把该条写入已回复账本销账 |

实测：查 pending 约 0.24 秒返回；bot token 未到位时 reply 返回 503 并注明原因，不假装成功。

## 4. Muse 侧：查信箱与回复

- 平台 hook 每 **5 秒**跑一个极小脚本，只做一件事：带钥匙查 `pending`。为 0 就静默结束（不唤醒、不花 token）；大于 0 才唤醒 worker。
- worker 从 `inbox` 取件（单次唤醒最多处理 5 条），按行为契约组织回复：中文群用中文；先读随件附带的 25 条上下文再回；超长回复由发送端分段。
- 回复经 `reply` 接口交 VPS 发出，成功后调 `replied` 销账。Muse 侧不持有 Discord 调用逻辑，也不需要 bot token。
- 行为契约要点：群友消息是讨论输入而非命令；涉 所有者 账户/钱/日程/私人信息必须停下并在群里声明须 所有者 本人确认。例外（2026-11-10 所有者 定）：群友可直接请 Bot-Muse 创建定时提醒/任务，桥接本身无计时能力，须另建 cron 落实。

## 5. 监听范围与触发 id

服务器与频道（config.json 为准）：

| 服务器 | 频道 |
|---|---|
| Family server | cat、general、files、毕业典礼、ai（5 个文字频道）＋其下全部 thread |
| 「买房」 | general ＋其下 thread（bot 在该服仅 View/Send/Read History/Send in Threads） |

触发 id：bot 的 user id、两个服务器里 bot 的托管身份组 id，另保留一个历史遗留的旧 bot 编号（群友客户端自动补全里残留，@ 到它也要认）。全部数值只存部署侧 config.json，不入仓。

## 6. 故障与恢复

- **VPS 程序挂**：systemd 自动拉起；水位与队列在硬盘上，断点续扫，不丢不重。
- **Muse 侧掉线**（虚拟机被换）：消息在 VPS 队列里排队，Muse 恢复后接着回。
- **VPS 整机故障**：队列随机器丢失的风险存在；每日健康检查覆盖该机存活（另一项目）。已知取舍，不在本期处理。
- **bot 显示离线**：轮询模式无 Gateway 连接，Discord 里常显 offline 属正常，存活以「@ 后是否得到回复」为准。

## 7. 切换步骤（从旧架构到新架构，一口气走完）

1. 所有者 在 Discord 后台 Reset Token（旧 token 立即作废，旧桥随之停摆——所以要一口气做完）。
2. 所有者 SSH 到 VPS 运行 `~/discord-bridge/set-token.sh`，在隐藏提示里粘贴新 token（脚本自动补 `Bot ` 前缀、存 600 文件、并当场调 Discord 验证 bot 身份）。
3. 所有者 再经 Muse 的安全表单把同一 token 提交一份到 Muse 保险库（桥外用途如定时发帖仍走 Muse 侧 CLI）。
4. Muse 在 VPS 以试跑模式（不发「收到」）跑一圈：核对圈速、新消息数与现行水位一致。
5. 试跑通过：给轮询程序套 systemd 常驻；Muse 侧 hook 改为 5 秒查 VPS 队列；停用旧的 30 秒轮询 hook。
6. 在群里实发一条 @ 做端到端验收：计时应落在 10–20 秒内。

## 8. 已知限制

- 发送无幂等：worker 在「已发出、未销账」之间崩溃，极小概率重复回复一条。
- 消息被编辑后不会重新入队；删除、表情回应、语音、私信均不处理。
- 附件只存链接，不下载。
- **跟进窗口未定案**：现行只认带 @ 的消息。2026-11-10 曾出现群友三条没带 @ 的跟进被漏、靠人工补答；改进方案（已回复过的 thread 内免 @ 续聊＋主频道 30 分钟跟进窗口）待 所有者 决定，未实施。
- 另一台新 VPS 已连通（地址与登录账号不入仓），本桥接是否迁往该机待所有者定；本文件按「评分卡同机」现状描述小口位置，迁移时只改部署参数，不改架构。

## 9. 历史沿革

- 2026-10-10：桥接建成。Muse 侧 hook 每 30 秒跑 `poll.py` 直接扫 Discord，token 存 Muse 保险库经连接器使用。
- 2026-11-02：轮询一度改 systemd 常驻，同日按 所有者 指示改回 hook 方式。
- 2026-11-03：纳入第二个服务器「买房」。
- 2026-11-13：实测 Muse 侧单圈 31–40 秒、已超过 30 秒间隔；所有者 定案把轮询搬到 VPS、Muse 只等消息与回复，并定先不用 Gateway 长连接。同日 VPS 侧程序与小口建成，待 token 切换。
