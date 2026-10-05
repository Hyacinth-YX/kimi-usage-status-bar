# kimi-status

Kimi Code CLI 的自定义 status line 脚本：复刻默认状态栏的布局和配色（权限模式徽章、模型、目录、git 状态徽章含修改行数统计），并在**末尾追加**套餐的 **5 小时滚动窗口用量** 和 **weekly（7 天）用量** 进度条。上下文用量（context）由 TUI 第二行默认展示，脚本不重复显示。

效果预览（终端内为彩色：模式徽章橙色加粗，目录/git 徽章暗灰，进度条按用量绿→黄→红渐变）：

```
Ask When Needed  K3-256k  ~/coding/my-project  main [+12 -3 ↑1]  5h ▬▬▬▬▬▬▬▬▬▬ 74% 01:34  7d ▬▬▬▬▬▬▬▬▬▬ 34% 10-09 15:34
```

git 徽章与官方默认完全一致：`分支 [+添加行数 -删除行数 ↑领先 ↓落后]`，有改动但无行数变化时显示 `±`。

进度条为低矮样式（垂直居中的 `▬`）：实心部分按用量变色，空槽为暗色轨道。如果你的字体把 `▬` 渲染得偏上/偏下，可改 `BAR_GLYPH` 为 `▄`（贴基线）或 `■`（方块）。

## 工作原理

- Kimi Code 支持在 `~/.kimi-code/tui.toml` 中配置 `[status_line].command`：TUI 每次刷新状态栏时，会通过 stdin 传入一个 JSON 快照（模型、目录、git 分支、权限模式、上下文用量等），脚本 stdout 的第一行会替换状态栏第一行（第二行的 context 用量仍是默认显示）。
- 自定义行虽然整体被套了一层默认文字色，但**内嵌的 ANSI 颜色码会生效**，所以脚本自己输出与官方主题一致的配色（dark/light 调色板根据 `tui.toml` 的 `theme` 自动选择，`auto`/自定义主题按 dark 处理）。
- 配额数据不在快照里，脚本通过 `GET https://api.kimi.com/coding/v1/usages` 获取，使用本机 `~/.kimi-code/credentials/kimi-code.json` 中的 OAuth token 认证（与 CLI 本身用的是同一份凭证）。
- git 状态徽章用**与 TUI 内置完全相同的命令**采集（`git branch --show-current`、`git status --porcelain`、`git diff --numstat HEAD`），按目录缓存在 `~/.kimi-code/cache/statusline-git.json`，15 秒有效期（与 TUI 一致），过期后台异步刷新。
- 官方对脚本的限制是 **300ms 执行上限、每秒最多调用一次**，所以配额和 git 查询都走本地缓存：状态栏刷新只读缓存文件，缓存过期时在后台异步刷新，绝不阻塞 UI。

### 无法复刻的默认元素

stdin 快照不包含以下信息，脚本无法 1:1 还原：`goal` 徽章、后台任务计数徽章、swarm/tower 模式徽章、模型名后的 `thinking: high` 后缀、右侧的轮换小提示（tips）。日常使用中这些很少出现或不重要；其余部分（模式徽章、模型、目录、git 徽章）均与默认一致。

## 前提条件

- 已安装 Kimi Code CLI 并通过 `/login` 登录（OAuth 方式，非 API key）
- Python 3.7+（只用标准库，无需安装任何依赖）
- 旧套餐用户可同时看到 5h 和 7d 两项；新套餐已取消 weekly 限额，7d 字段可能恒为 0 或不存在

## 安装

```bash
# 1. 把脚本放到固定位置（以本仓库为例）
git clone https://github.com/Hyacinth-YX/kimi-usage-status-bar.git ~/kimi-status   # 或直接复制 statusline.py
chmod +x ~/kimi-status/statusline.py

# 2. 预热缓存（首次会先显示占位符，预热后立即有数据）
~/kimi-status/statusline.py --refresh

# 3. 在 ~/.kimi-code/tui.toml 末尾追加：
cat >> ~/.kimi-code/tui.toml << 'EOF'

[status_line]
command = "~/kimi-status/statusline.py"
EOF
```

然后**在 Kimi Code 里执行 `/reload-tui`**（或重启 CLI）即可生效。

> 注意：`command` 中的路径必须是脚本的真实路径；如果使用 `~`，请确认你的 CLI 版本会做展开，否则写绝对路径更稳妥。

## 自定义

直接编辑 `statusline.py` 顶部的常量：

| 常量 | 默认 | 说明 |
| --- | --- | --- |
| `CACHE_TTL` | `180` | 配额缓存有效期（秒），过期后后台异步刷新 |
| `ERR_CACHE_TTL` | `60` | 拉取失败后的重试间隔（秒） |
| `BAR_WIDTH` | `10` | 进度条格数 |
| `BAR_GLYPH` | `"▬"` | 进度条字符（垂直居中矩形）；可选 `▄`（半高贴基线）、`■`（方块） |

不想要某一段（如 git 分支），删掉 `main()` 里对应的 `parts.append(...)` 即可。进度条配色阈值在 `usage_color()` 里：默认 <50% 绿、50–80% 黄、≥80% 红。

## 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| 状态栏变回默认样式 | 脚本执行失败或超时，CLI 自动回退到内置布局；手动运行 `echo '{}' \| ./statusline.py` 看报错 |
| 显示 `5h ▬▬▬▬ ?%` | 缓存还没生成，运行一次 `statusline.py --refresh` |
| 显示 `quota: login expired` | OAuth token 过期（接口返回 401）；正常使用一次 CLI 让它自动刷新凭证，或重新 `/login` |
| 显示 `quota: unavailable` | 网络或接口异常，一分钟后自动重试 |
| git 徽章不显示或没有统计 | 当前目录不是 git 仓库时属于正常现象；修改统计有最长 15 秒延迟（与 TUI 一致），刚保存文件稍等片刻即会更新 |
| 颜色看起来和默认不一样 | 脚本按 `tui.toml` 的 `theme` 选 dark/light 调色板；自定义主题会按 dark 基础色处理 |
| 想看 TUI 实际传了什么 | 每次刷新都会把快照存到 `~/.kimi-code/cache/statusline-stdin.json` |

## 卸载

1. 删除 `~/.kimi-code/tui.toml` 中的 `[status_line]` 段
2. `/reload-tui` 或重启 CLI
3. （可选）删除脚本和缓存文件 `~/.kimi-code/cache/statusline-quota.json`、`~/.kimi-code/cache/statusline-git.json`

## 说明与免责

`/coding/v1/usages` 是 Kimi Code CLI 内部使用的接口，**未在官方文档中公开**，字段和可用性可能随版本变动。脚本所有失败路径都有降级处理，最坏情况只是配额段显示占位符，不影响 CLI 正常使用。

- 官方 status line 文档：[Configuration files — `[status_line]`](https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/config-files.html)
- 官方配额说明：[Membership — Quota and Limits](https://www.kimi.com/code/docs/en/kimi-code/membership.html)

## License

[MIT](LICENSE)
