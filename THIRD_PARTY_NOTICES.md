# 第三方素材说明

本项目代码以 [MIT](LICENSE) 发布。以下是**非代码素材**的来源与授权情况。

## 开机动画

内置开机动画 `assets/boot.webp` 由下列项目的视频素材转码而来：

| 项 | 内容 |
|---|---|
| 项目 | **dsh-video-skin** |
| 仓库 | <https://github.com/Something11235/dsh-video-skin> |
| 许可 | MIT |
| 原始素材 | `assets/boot.mp4`（1280×720 / 24fps / 8.06s） |
| 本项目产物 | `assets/boot.webp`（900×506 / 24fps / 2.0s / 959 KB） |

转码方式（截取 0–6 秒并加速 3 倍，去掉静止尾巴）：

```bat
ffmpeg -i boot.mp4 -t 6 -vf "setpts=PTS/3,fps=24,scale=900:-1:flags=lanczos" ^
       -c:v libwebp -loop 0 -q:v 50 -an assets/boot.webp
```

启动器图标 `assets/source.jpg` → `assets/logo.png` 使用同一角色的形象，来源同上。

> **一点提醒**：`dsh-video-skin` 的 README 只声明了软件代码的 MIT 许可，未单独说明 `assets/`
> 目录内视频素材的授权范围。个人使用没问题；如需商用或大规模再分发，建议先联系原作者确认。

## 开机动画可被替换

内置动画只是默认值。把 `boot.webp` 或 `boot.gif` 放到**启动器 exe 同目录**即可替换，
不需要重新打包；删掉外部文件会回落到内置的那份。详见 [用户指南 §12](docs/用户指南.md)。

所以如果你有自己的动画、或者不希望使用上述素材，直接替换即可，不必改代码。

## 运行依赖

| 依赖 | 许可 | 用途 |
|---|---|---|
| [PySide6](https://pypi.org/project/PySide6/) / Qt 6 | LGPL-3.0 | GUI 框架 |
| [psutil](https://pypi.org/project/psutil/) | BSD-3-Clause | 进程信息采集 |
| [PyInstaller](https://pypi.org/project/pyinstaller/) | GPL-2.0 + 例外条款 | 打包（仅构建期） |
