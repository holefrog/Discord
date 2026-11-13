# Discord 添加 App 和 Bot 操作指南

> 给 所有者 操作用。以 Bot-Muse 为例，Bot-Hark 完全同理，只是名字换成 `App-Bot-Hark` / `Bot-Hark`。
> 先理解一组关系：**一个 App 只能挂一个 Bot**。App 是后台容器（管配置），Bot 是进服务器说话的员工账号。`Client Secret` 是 App 的，`Token` 是 Bot 的——复制时别拿错，拿错会 401。

## 第一步：建应用（Application）

1. 打开 https://discord.com/developers/applications，登录。
2. 右上角 **New Application**，名字填 `App-Bot-Muse`（Bot-Hark 就填 `App-Bot-Hark`），同意条款后 Create。

## 第二步：加机器人（Bot）

1. 左侧点 **Bot** → **Add Bot** → 确认。
2. 往下找到 **Privileged Gateway Intents**，打开 **Message Content Intent**（不读消息内容就不用开，但我们要读上下文，必须开）。
3. 想改机器人显示名：在 Bot 页点用户名旁的编辑，或以后再改也行。

## 第三步：复制 Token（关键，别拿错）

1. 还在 **Bot** 页，找到 **Token** 一栏，点 **Reset Token** → **Copy**，得到一串很长、中间有两个点的字符。**它只显示这一次**，关掉页面就再也看不到原文，只能再 Reset 生成新的。
2. ⚠️ **General Information 页的 Client Secret 不是它**，复制那个会 401。
3. ⚠️ 复制完别再点 Reset Token，每点一次旧的立刻作废。
4. ⚠️ 送达方式只有两种，**不要贴在聊天里、不要写进任何文档或仓库**（贴过一次就必须作废重换；本指南旧版本曾误写入 token，已清除）：
   - 给 Muse：走 Muse 的安全表单提交，格式为 `Bot` + 一个空格 + token，整行粘贴（例如 `Bot MTk4…xxxx`）。
   - 给 VPS：SSH 登录 VPS 后运行 `~/discord-bridge/set-token.sh`，在隐藏输入提示里粘贴（只粘 token 本体即可，脚本会自动补 `Bot ` 前缀、存成 600 权限文件，并当场验证）。

## 第四步：把机器人请进服务器

1. 左侧 **OAuth2** → **URL Generator**。
2. Scopes 勾选 **`bot`**。
3. 下面 Bot Permissions 勾选：
   - View Channels（看频道）
   - Send Messages（发消息）
   - Read Message History（读历史）
4. 把页面下方生成的 URL 复制到浏览器打开 → 选择你的服务器 → Authorize。
5. 回服务器看成员列表，机器人已在里面（带 BOT 标志）。

## 第五步（可选）：建共享身份组 Bots

1. 服务器名旁下拉 → 服务器设置 → 身份组 → 创建身份组，起名 `Bots`。
2. Manage Members → 把 Bot-Muse 加进去；Bot-Hark 进来后也加进去。
3. 把该组的 id 发给桥接维护方，加入 @ 触发名单（@Bots 时两个机器人都会出来）。
4. 说明：机器人自带的托管组（managed role）删不掉、加不进别人，不用管它，留着就行。

## 常见坑

| 现象 | 原因 |
|---|---|
| API 返回 401 | 复制的是 Client Secret 而不是 Token；或复制后又点了 Reset Token；或粘贴时没带 `Bot ` 前缀 |
| 机器人读不到消息内容 | Message Content Intent 没开 |
| 机器人发不出消息 | 邀请链接里权限没勾全，重新生成链接再进一次 |
| @ 机器人没反应 | @ 的是身份组而非用户（或反之）——桥接侧两种都认，仍无反应请检查桥接状态 |
| 后台 Bot 页名字和服务器里不一致 | 客户端缓存，Ctrl+R 重载或重启客户端 |
