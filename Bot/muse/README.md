# Bot/muse —— Muse 侧部署包（给接任的 Muse）

这个目录是桥接的**可部署全套**：VPS 侧代码、Muse 侧定义与脚本、行为契约、定时任务，脱敏后入仓（真实地址、id、钥匙只在部署侧，见文末）。架构与原理看仓库根 README 与上级技术文档，本文件只讲「照着装」。

## 目录

| 路径 | 装到哪 |
|---|---|
| `vps-code/poll_vps.py`、`run-loop.sh`、`set-token.sh` | VPS `~/discord-bridge/`（已在运行；除非重建机器，不要覆盖线上版，先 diff） |
| `vps-code/discord-bridge-poll.service` | VPS `/etc/systemd/system/`（unit 里的 `<SSH_USER>` 换成实际登录用户） |
| `vps-code/discord-bridge-queue.service`、`queue_server.py` | 已下线的临时队列服务，存档，不要装 |
| `vps-code/config.example.json` | 结构示例；真实 `config.json` 在 VPS 上已有，部署时从机器上取，不要照示例手填 |
| `muse-side/discord-mention-watch.json` | 用你自己的 hook 工具按此定义重建 hook（5 秒间隔、投递 main） |
| `muse-side/discord-bridge-vps-check.sh` | 上面 hook 的脚本，放你的 `~/hooks/scripts/`；把 `<VPS_IP>` 换成真实地址 |
| `muse-side/WORKER-PROMPT-VPS.md` | hook 定义里 worker 指令的纯文本版（与 JSON 内的 prompt 同内容，改词时两处同步） |
| `muse-side/WORKER.md` | 行为契约，桥的安全红线以此为准 |
| `crons/oq-job-prep-weekly-reminder.md` | 用你自己的 cron 工具按全文重建（频道 id 占位符向 所有者 索取真实值） |

## 部署步骤（新 Muse 接管时）

1. 读仓库根 README + 技术文档 + 本文件。
2. 用自己的 SSH 钥匙登录 VPS 自检（公钥需先由 所有者 加入 authorized_keys）：`systemctl is-active discord-bridge-poll rating-card` 均为 active；`tail ~/discord-bridge/poll.log` 圈时在 7–13 秒量级。**不要动 VPS 上的服务、状态文件与 token**——它们与账号无关，原地运行。
3. 取小口钥匙：**不用向任何人要**——用你的 SSH 登录 VPS，读 `~/discord-bridge/hook-token` 的现值，存进你自己的 secrets 区（hook 脚本从 `~/hooks/secrets/discord-bridge-newvps-hook-token` 读它）。轮换与否随你，轮换法见技术文档 §8，那是加固、不是迁移必需。
4. 重建 hook：脚本落位、定义照 `discord-mention-watch.json`（prompt 里的 `<VPS_IP>` 换真实地址），dry run 一次——队列为空应静默、有待回复应报数。
5. 重建 OQ 提醒 cron（先向 所有者 确认是否仍需要、投递到哪个对话）。
6. 在群里实发一条 @ 做端到端验收：基准——「收到」5–13 秒、正式回复 44–65 秒（大头是 agent 思考，属正常）。
7. 交班切换须 所有者 明确下令，顺序是：前任先停用其 hook/cron、回读验证、查队列积压，并经 所有者 转交一份「已停清单」给你作凭据；你收到清单后再启用自己这侧，两边不得同时回复同一条。前任停没停以「已停清单」为准，不要用其他状态猜。

## 脱敏对照（部署时向 所有者 / 机器侧取真实值）

- `<VPS_IP>`：桥所在 VPS 的地址（小口也在此，:3000）。
- `<SSH_USER>`：该 VPS 的 SSH 登录用户；端口 22122。
- 各类 `<...ID>`：Discord 的 bot/频道/身份组/用户编号——真实 `config.json` 在 VPS `~/discord-bridge/` 里现成有，接管时整表复制，不要手抄。
- bot token：任何 Muse 都不经手；只在 VPS `~/discord-bridge/token.txt`，轮换流程见上级《建 Bot 操作指南》。
