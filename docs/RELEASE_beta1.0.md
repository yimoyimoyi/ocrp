# ORCP Beta 1.0

跨平台视频/图片 OCR 字幕提取工具，支持 PaddleOCR、OpenAI Vision、WhisperX 语音识别、LLM 纠错。

## 下载

| 压缩包 | 说明 |
|--------|------|
| `orcp-win-gpu.zip` | GPU 版（分卷压缩），含 small ASR 模型 |
| `orcp-win-cpu.zip` | CPU 版，含 small ASR 模型 |

解压后点击 `orcp_gui.bat` 即可启动，无需额外配置。

> Linux 用户请从源码安装：`bash setup.sh && bash orcp_gui.sh`

## ASR 模型下载

首次启用 ASR 时，模型会自动下载至 `models/asr/`。也可手动下载后放入该目录：
- [tiny](https://huggingface.co/Systran/faster-whisper-tiny) · [base](https://huggingface.co/Systran/faster-whisper-base) · [small](https://huggingface.co/Systran/faster-whisper-small)
- [medium](https://huggingface.co/Systran/faster-whisper-medium) · [large-v2](https://huggingface.co/Systran/faster-whisper-large-v2) · [large-v3](https://huggingface.co/Systran/faster-whisper-large-v3)

> 模型越大精度越高但速度越慢，tiny/small 适合低配设备，large-v3 精度最佳。

## GPU ASR 加速（可选）

需 cuDNN 8.9 for CUDA 12.x，从 [NVIDIA cuDNN](https://developer.nvidia.com/cudnn) 下载（需注册），将 DLL 放入 `models/asr/lib/`。缺失则自动回退 CPU。

详细文档：[README](README.md) · [贡献指南](CONTRIBUTING.md)
