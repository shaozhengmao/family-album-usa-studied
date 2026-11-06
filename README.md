# 走遍美国学习版

> 《走遍美国》(Family Album U.S.A.) 英语学习版：26 集完整台词、逐句中文翻译、音频播放、全局搜索，纯前端无后端，开箱即用。

---
## ⚠️ 版权声明与免责说明

如原作者或出版社认为本项目侵犯了相关权益，请通过 GitHub Issues 联系，本人将立即删除相关内容。

---


## 功能特性

- **26 集 / 78 幕 / 6319 句台词**：完整覆盖《走遍美国》全剧集
- **逐句中文翻译**：英文与中文对照显示，可一键切换显隐
- **三种播放粒度**（核心）：
  - **连播**：整幕连续播放，当前句自动高亮跟随
  - **循环**：当前句连播 2 遍（按钮右侧显示 1/2 计数）后自动进下一句，全程不用碰键盘，适合磨耳朵
  - **逐句**：播完一句自动暂停并定位到下一句，适合听写 / 跟读
- **盲听模式**：文本整块模糊（不是隐藏，版面不跳动）；点任意一句即揭晓并从那句开始播，再点一次收回遮罩。想一次看全幕，直接退出盲听
- **句级定位**：点台词列表任意一句即从那一句开始播；桌面端另有上一句 / 下一句按钮，移动端仅在盲听时显示（非盲听时点句子就能跳）
- **播放速度控制**：0.75× / 1× / 1.25× / 1.5×，收在一个下拉里，不占版面
- **全局搜索**：跨 26 集搜索英文台词、中文翻译或集名，点击跳转（`Ctrl/Cmd + K` 唤起）
- **吸顶播放条**：滚到任何位置播放控件都在手边
- **响应式设计**：桌面双栏、手机底部导航栏
- **纯静态部署**：无需后端，可直接托管到 GitHub Pages

---

## 在线预览

本项目可直接部署到 GitHub Pages，无需后端服务器：

**[👉 在线演示](https://shaozhengmao.github.io/family-album-usa-studied/)**

> 部署方式见下方「GitHub Pages 部署」章节。推送 `main` 分支后，仓库自带的 GitHub Actions workflow 会自动部署。

---

## 项目结构

```
family-album-usa-studied/
├── audio-acts/          # 78 个独立音频文件（每幕一个 mp3）
│   ├── e01_a1.mp3
│   ├── e01_a2.mp3
│   └── ... (e01 ~ e26，每集 3 个)
├── episodes.json        # 剧集数据（英文基于原版，中文翻译原创）
├── index.html           # 单页应用（完全原创代码）
├── .nojekyll            # 禁用 GitHub Pages 的 Jekyll 处理
├── .github/workflows/   # 自动部署到 GitHub Pages 的 workflow
└── README.md            # 本文件
```

### 数据格式

```json
{
  "episodes": [{
    "id": 1,
    "title": "46 Linden Street",
    "titleCn": "林登大街46号",
    "totalLines": 268,
    "acts": [{
      "label": "Act I",
      "audio": "audio-acts/e01_a1.mp3",
      "lineCount": 87,
      "lines": [{
        "t": 34.18,
        "start": 12.80,
        "end": 16.00,
        "en": "Excuse me. My name is Richard Stewart.",
        "cn": "打扰一下，我叫 Richard Stewart。"
      }]
    }]
  }]
}
```

**音频播放说明**：
- 每幕使用独立的 MP3 文件（`audio-acts/e{集数}_a{幕数}.mp3`）
- `start` / `end`：用 whisper 强制对齐重算出的句级区间（秒），**优先使用**
- `t`：原始时间戳，仅作历史参考。它与实际音频严重不符（见下节），前端只在某句没有 `start` 时退回用它

---

## 时间戳对齐（务必了解）

原始数据的句级时间戳与 mp3 实际内容对不上：

| 问题 | 规模 |
|---|---|
| 整幕时间戳全为 0（ep18 ActII/III、ep19 全三幕） | 198 句 |
| 时间戳超出音频长度（物理上播不到） | 124 句 |
| 有明显问题的幕 | 26 / 78 |

根因是 ep1–15 用的是裁掉静默的音轨（每集约 12.6 分钟），ep16–26 用的是完整原声（每集约 23.5 分钟），而时间戳统一来自精简版时间轴，于是误差随进度累积，**不是加个固定偏移量能修正的**。

因此用 `tools/align_timestamps.py` 对全部 78 幕重跑了一遍词级识别 + 逐句对齐：

```bash
pip install faster-whisper
HF_ENDPOINT=https://hf-mirror.com python tools/align_timestamps.py --model small
python tools/merge_alignment.py --write     # 合并进 episodes.json
```

平均每幕命中率 0.90+，`align_out/_report.json` 里有逐幕明细。命中率低的句子（大多是画面文字，音频里根本没念）会保留原 `t`，前端退回用旧值。

> 注意：`align_timestamps.py` 里**不能传 `initial_prompt`**。实测传了之后 whisper 会把 prompt 中的句子当成"已转录的前文"直接跳过，导致整幕开头几句匹配率归零。

---

## 本地使用

### 方式一：直接打开（基础功能）

用浏览器直接打开 `index.html` 文件即可查看，但部分浏览器会因安全策略拦截 `fetch('episodes.json')`，**推荐使用方式二**。

### 方式二：本地服务器（**必须**，且必须支持 Range）

```bash
python3 tools/dev_server.py 8080
# 然后访问 http://127.0.0.1:8080
```

**不要用 `python3 -m http.server`**：它不响应 HTTP Range 请求，浏览器会把音频的
`seekable` 判定成 `[0,0]`。表现是能播放、但「循环」「逐句」「点某句从那里播」全部失效
（`currentTime` 赋值被静默丢弃），非常难查。`tools/dev_server.py` 补了 206 响应。
GitHub Pages 原生支持 Range，所以线上没问题。

其他可用方案：`npx serve .`、nginx、任何支持 206 的静态服务器。

---

## GitHub Pages 部署

本项目**纯静态**，可直接部署到 GitHub Pages，**完全免费**。

### 自动部署（推荐）

仓库已包含 `.github/workflows/deploy.yml`，推送 `main` 分支会自动部署：

1. Fork 本仓库到你自己的 GitHub 账号
2. 进入仓库 → **Settings** → **Pages**
3. **Source** 选择 **GitHub Actions**
4. 推送代码到 `main` 分支，等待 Actions 跑完即可访问：
   `https://你的用户名.github.io/family-album-usa-studied/`

---

## 键盘快捷键

| 快捷键 | 功能 |
|--------|------|
| `空格` | 播放 / 暂停 |
| `←` / `→` | 上一句 / 下一句 |
| `R` | 重播当前句 |
| `B` | 开关盲听 |
| `Ctrl/Cmd + K` | 打开 / 关闭搜索 |
| `Esc` | 关闭搜索弹窗 |

---

## 浏览器支持

- Chrome / Edge / Firefox / Safari 最新版
- 支持移动端浏览（手机、平板自适应）

### 移动端特性

- 底部导航栏：首页、搜索、主题切换、翻译切换
- 自适应字体：根据屏幕宽度自动调整文字大小
- 触控优化：按钮和台词区域适合手指点击

---

## 许可证

MIT

> 本项目仅供英语学习交流使用，不得用于商业用途。
