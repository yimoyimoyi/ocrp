# ASR 模型下载与存放说明

## 模型存放位置

将下载的模型文件按以下结构放入此目录：

```
models/asr/
├── models--Systran--faster-whisper-large-v3/   ← HF 缓存格式（推荐）
│   ├── faster-whisper-large-v3/
│   │   ├── model.bin
│   │   ├── config.json
│   │   ├── tokenizer.json
│   │   └── vocabulary.json
│   └── snapshots/
│       └── <commit-hash>/
│           ├── model.bin
│           └── ...
├── faster-whisper-large-v2/                    ← 直接目录格式（可选）
│   ├── model.bin
│   └── config.json
└── lib/                                        ← cuDNN 8 DLL（GPU ASR 需要）
    ├── cudnn_ops_infer64_8.dll
    ├── cudnn_cnn_infer64_8.dll
    └── cudnn64_8.dll
```

## 下载方式

### 方式一：HuggingFace 镜像下载（推荐，国内加速）

使用 `hf-mirror.com` 镜像下载，速度更快：

```bash
# 设置镜像源
set HF_ENDPOINT=https://hf-mirror.com

# 使用 huggingface-cli 下载（需先安装：pip install huggingface_hub）
huggingface-cli download Systran/faster-whisper-large-v3 --local-dir models/asr/models--Systran--faster-whisper-large-v3
```

或直接浏览器访问：
- 镜像站：https://hf-mirror.com/Systran/faster-whisper-large-v3
- 官方站：https://huggingface.co/Systran/faster-whisper-large-v3

### 方式二：手动下载

**核心模型（必选，选一个模型大小）：**

| 模型 | 大小 | 镜像 URL | 官方 URL |
|------|------|----------|----------|
| tiny | ~75MB | https://hf-mirror.com/Systran/faster-whisper-tiny | https://huggingface.co/Systran/faster-whisper-tiny |
| base | ~145MB | https://hf-mirror.com/Systran/faster-whisper-base | https://huggingface.co/Systran/faster-whisper-base |
| small | ~500MB | https://hf-mirror.com/Systran/faster-whisper-small | https://huggingface.co/Systran/faster-whisper-small |
| medium | ~1.5GB | https://hf-mirror.com/Systran/faster-whisper-medium | https://huggingface.co/Systran/faster-whisper-medium |
| large-v2 | ~3GB | https://hf-mirror.com/Systran/faster-whisper-large-v2 | https://huggingface.co/Systran/faster-whisper-large-v2 |
| **large-v3** | ~3.1GB | https://hf-mirror.com/Systran/faster-whisper-large-v3 | https://huggingface.co/Systran/faster-whisper-large-v3 |

点击链接 → "Files and versions" → 下载 `model.bin`、`config.json`、`tokenizer.json`、`vocabulary.json`，放入对应的 `models/asr/models--Systran--faster-whisper-<模型名>/faster-whisper-<模型名>/` 目录。

### 方式三：第一次运行时自动下载

首次语音识别时，程序会自动从 HuggingFace Hub 下载模型（优先使用 `hf-mirror.com` 镜像）。

下载位置：
- 配置的 `model_dir`（默认 `models/asr`）
- 或系统缓存：`C:\Users\<用户名>\.cache\huggingface\hub\`

## cuDNN 8 DLL（GPU ASR 需要）

GPU 语音识别需要 cuDNN 8 DLL，放入 `models/asr/lib/` 目录：

| 文件 | 说明 |
|------|------|
| `cudnn_ops_infer64_8.dll` | 基础 ops |
| `cudnn_cnn_infer64_8.dll` | 卷积推理 |
| `cudnn64_8.dll` | 运行时 |

下载地址：https://developer.nvidia.com/cudnn （需注册 NVIDIA 开发者账号）

选择 cuDNN 8.x for CUDA 12.x 版本，解压后将 `bin/*.dll` 复制到 `models/asr/lib/`。

## 推荐配置

| 场景 | 模型 | 语言 |
|------|------|------|
| 中文字幕提取 | large-v3 | zh |
| 快速测试 | base | zh |
| 多语言/英文 | large-v3 | auto |

## 验证

在 ORCP 配置面板 → "语音识别"标签页 → "模型目录" 中确认路径为 `models/asr`，点击"应用语音识别设置"保存。程序启动语音识别时会优先从此目录加载。

## 配置文件

ASR 配置位于 `config/asr_engines.json`：

```json
{
  "model_size": "models--Systran--faster-whisper-large-v3",
  "model_dir": "models/asr",
  "language": "zh",
  "device": "cuda",
  "hf_endpoint": "https://hf-mirror.com"
}
```

- `model_size`：模型名称或本地路径
- `model_dir`：模型存放目录
- `hf_endpoint`：HuggingFace 镜像源（国内加速）
