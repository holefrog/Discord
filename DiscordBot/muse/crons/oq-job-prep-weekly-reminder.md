# OQ 求职准备每周提醒

每周五 15:00（America/Toronto）在 所有者 的 Discord 服务器 #求职准备 频道给 OQ 发一条求职准备进度提醒。OQ 提出、所有者 于 2026-11-10 批准（同日 所有者 定：群成员都有权让 Bot-Muse 建定时任务）。

## 通道（2026-11-13 所有者 选定 B 方案：全走新 VPS，不用保险库 CLI）
- 读写都走新 VPS muse-free-vm 的桥接口（评分卡服务 :3000），请求头带 `X-Bridge-Token: $(cat ~/hooks/secrets/discord-bridge-newvps-hook-token)`（读文件取值，不要打印 token）。
- **不要再用** `~/workspace/discord-bridge/context.py` / `send.py`：它们走保险库里的旧 bot token，该 token 已于 2026-11-13 作废，必然 401。

## 时间窗（先判日期，再做事）
- 今天（多伦多日期）早于 2026-12-25：**不发帖、不汇报**，直接结束本次运行（系列尚未开始）。
- 2026-12-25 至 2027-04-30 之间：按下面流程发帖。
- 晚于 2027-04-30：不发帖；向 所有者 汇报「OQ 求职准备每周提醒系列已结束」，请他删除本 cron（oq-job-prep-weekly-reminder）并关闭 tracked item「OQ 求职准备每周提醒」。

## 发帖流程
1. 先读 #求职准备 频道近期消息看 OQ 报过的进度（哪些已完成、哪些在做）：`curl -s -H "X-Bridge-Token: $(cat ~/hooks/secrets/discord-bridge-newvps-hook-token)" "http://<VPS_IP>:3000/api/bridge/messages?channel_id=<OQ提醒频道ID>&limit=25"`，返回 JSON 的 items（最新在前，含 author、content、timestamp）。她报过的以她为准，没报的按默认排期推进；遇到她说的考试周，本周只发一句「考试周冻结，考完继续」，不推新任务。
2. 发帖：把正文写进临时文件，再用 python3 读文件组 JSON 发出：`curl -s -X POST -H "X-Bridge-Token: $(cat ~/hooks/secrets/discord-bridge-newvps-hook-token)" -H "Content-Type: application/json" --data @<含 {"channel_id":"<OQ提醒频道ID>","content":正文} 的 JSON 文件> http://<VPS_IP>:3000/api/bridge/reply`，返回 `{"ok":true}` 才算发出。开头 @ OQ（<@<OQ_USER_ID>>）。格式按 所有者 的 Discord 约定：**加粗小标题**、段间空行、条目用 `· `，简短，别写成长文。
3. 发完向 所有者 一行汇报：第几期、主题是什么。

## 内容依据（OQ 2026-10-22 的毕业前补经验计划，总预算 35–40 小时，2027 年 4 月底毕业）
- 第 0 层（1–2 小时，一次过）：四年实验课技术盘点写进简历（PCR、电泳、DNA/RNA 提取、无菌操作、显微镜、R/Python 作业），建 GitHub/LinkedIn，简历压成一页。
- 第 1 层 · 证书包（约 5 小时，一周拿完）：WHMIS 2015 在线证 + 两门 GMP 微课。
- 第 2 层 · 一个公开项目（每周 2 小时 × 12–14 周）：Galaxy Training Network 入门路径 → 选 GEO 数据集复现一篇已发表的 RNA-seq 分析 → 关键步骤改成 R 脚本放 GitHub。只做一个。
- 第 3 层 · 一批邮件（2 小时，一次发完）：给 8–10 位计算方向教授发邮件，只谈远程（冬季远程处理数据 3–4 小时/周，顺带暑期 USRA），没回复成本到此为止。
- 明确不做：湿实验志愿、100 小时级长课、第二个项目、考试月开新坑。

## 默认排期（周五，多伦多日期；有 OQ 实际进度时按进度调整）
- 12-25 开场：第 0 层 + 第 1 层启动
- 1-1、1-8：第 1 层证书包（WHMIS、两门 GMP）
- 1-15：第 3 层邮件一次发完 + 第 2 层启动（GTN 入门路径）
- 1-22 至 3-26：第 2 层每周推进（GTN 路径 → GEO 数据集 → RNA-seq 复现 → R 脚本上 GitHub），每周只给本周 2 小时的具体目标
- 4-2、4-9：收尾（项目 README、把四行产出补进简历：技能清单、两张证书、项目链接、远程经历）
- 4-16、4-23：毕业前检查（简历终版、LinkedIn 更新、投递清单）
- 4-30：最后一期，收官总结

Discord 发不出去（VPS 接口报错）时不要重试刷屏，向 所有者 汇报失败原因即可。
