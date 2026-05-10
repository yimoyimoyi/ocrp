# WhisperX 模型下载与存放说明

## 模型存放位置

将下载的模型文件按以下结构放入此目录：

```
models/asr/
├── large-v3/                  ← 核心 Whisper 转录模型
│   ├── model.bin
│   └── config.json
├── models--Systran--faster-whisper-large-v3/   ← HF 缓存格式（可选）
│   └── ...
└── wav2vec2-large-xrlsr-53/   ← 字级对齐模型（可选，禁用字级时间戳则不需要）
```

## 下载方式

### 方式一：HuggingFace Hub（手动下载）

**核心模型（必选，选一个模型大小）：**

| 模型 | 大小 | URL |
|------|------|-----|
| tiny | ~75MB | https://huggingface.co/Systran/faster-whisper-tiny |
| base | ~145MB | https://huggingface.co/Systran/faster-whisper-base |
| small | ~500MB | https://huggingface.co/Systran/faster-whisper-small |
| medium | ~1.5GB | https://huggingface.co/Systran/faster-whisper-medium |
| large-v2 | ~3GB | https://huggingface.co/Systran/faster-whisper-large-v2 |
| **large-v3** | ~3.1GB | https://huggingface.co/Systran/faster-whisper-large-v3 |

点击链接 → "Files and versions" → 下载 `model.bin` 和 `config.json`，放入对应的 `models/asr/<模型名>/` 目录。

**对齐模型（可选，仅字级时间戳需要）：**

```
https://huggingface.co/ctranslate2/wav2vec2-large-xlsr-53-zh-cn
```

### 方式二：第一次运行时自动下载

首次语音识别时，WhisperX 会自动从 HuggingFace Hub 下载模型到系统缓存目录：

```
C:\Users\<用户名>\.cache\whisperx\           ← 核心模型
C:\Users\<用户名>\.cache\huggingface\hub\    ← 对齐模型 + VAD
```

下载完成后，将此缓存目录的内容复制到 `models/asr/` 即可离线使用。

## 推荐配置

| 场景 | 模型 | 语言 |
|------|------|------|
| 中文字幕提取 | large-v3 | zh |
| 快速测试 | base | zh |
| 多语言/英文 | large-v3 | auto |

## 验证

在 ORCP 配置面板 → "语音识别"标签页 → "模型目录" 中确认路径为 `models/asr`，点击"应用语音识别设置"保存。程序启动语音识别时会优先从此目录加载。
