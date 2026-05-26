<div align="center">

# ORCP

**クロスプラットフォーム動画/画像 OCR 字幕抽出ツール**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-yellow.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey.svg)]()

PaddleOCR · OpenAI Vision · Ollama · LlamaCpp · WhisperX · LLM補正 · 文分割

</div>

---

🌐 [中文](../README.md) | [English](README_EN.md)

ORCP は、動画・音声・画像からテキストを抽出するフル機能のデスクトップ字幕抽出ツールです。マルチエンジン OCR、オフライン音声認識、LLM 補正と文分割を統合し、直感的な PyQt5 GUI を提供します。

## 機能

### コア機能

| 機能 | 説明 |
|------|------|
| **マルチエンジン OCR** | PaddleOCR（ローカル GPU/CPU）、OpenAI Vision、Ollama Vision、LlamaCpp |
| **音声認識 (ASR)** | faster-whisper によるオフライン ASR、サブプロセスで CUDA 環境を分離 |
| **AI 補正 + 翻訳** | OpenAI 互換 API によるテキスト校正・翻訳、ストリーミング + JSON モード対応 |
| **LLM 文分割** | CoT（思考連鎖）による断片テキストの意味的結合、原文整合性検証付き |
| **校正モード** | 補正/翻訳後の二次 LLM チェック、文法・用語の問題を修正 |
| **ストリーミング字幕** | センチネル検出（単語数急減で台詞終了を判定）+ 類似度重複排除 + バッファ |
| **バッチ処理** | 複数ファイルキュー、`output/` に自動エクスポート |

### LLM 機能

| 機能 | 説明 |
|------|------|
| **統合ゲートウェイ** | 全 LLM 呼出を単一 `ask_llm()` エントリに統一、指数バックオフ再試行 + 応答キャッシュ |
| **RPM レート制限** | スライディングウィンドウ制限で API スロットリングを防止 |
| **マルチプリセット** | 複数 API 接続設定（OpenAI / DeepSeek / Ollama / Volcano Engine 等）、ワンクリック切替 |
| **接続テスト** | ワンクリックで API 接続性と認証情報を検証 |
| **プロンプトテンプレート** | ビジュアルエディタ、`{raw_text}` `{context}` `{environment}` 等のプレースホルダ対応 |
| **テンプレート/カスタム切替** | テンプレート上書きモードとカスタム参照モードを切替 |

### インターフェース機能

- 動画/画像プレビュー、ドラッグで ROI 領域を描画
- マルチ領域でエンジンとプロンプトを個別設定
- 結果テーブル：ビジュアル編集、検索置換、フィルタリング、ソート
- 検索バーと結果テーブルのスタイル統一
- ダーク/ライトテーマのワンクリック切替
- 折りたたみ設定パネル（デコードパラメータ / VAD / 領域ソート）
- 処理モード切替時の自動エンジンウォームアップ

### 対応フォーマット

| 種類 | フォーマット |
|------|-------------|
| **動画入力** | MP4, MKV, AVI, MOV, WebM |
| **音声入力** | MP3, WAV, FLAC, OGG |
| **画像入力** | PNG, JPG, BMP |
| **字幕出力** | SRT, TXT, JSON, CSV |

## クイックスタート

### 要件

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) パッケージマネージャ（推奨）
- FFmpeg（セットアップスクリプトが自動処理）
- GPU モード：NVIDIA ドライバ + CUDA Toolkit

### インストール

**Windows：**
```batch
git clone https://github.com/yimoyimoyi/orcp.git
cd orcp
setup.bat              # GPU 自動検出、依存関係インストール
```

**Linux：**
```bash
git clone https://github.com/yimoyimoyi/orcp.git
cd orcp
bash setup.sh          # GPU 自動検出、システム依存関係インストール
```

セットアップスクリプトオプション：
| オプション | 説明 |
|-----------|------|
| `--cpu` | CPU 専用モードを強制 |
| `--gpu` | GPU モードを強制 |
| `--no-ffmpeg` | FFmpeg インストールをスキップ |

### 起動

```bash
# クロスプラットフォーム
uv run python ocr_gui.py

# 起動スクリプト
orcp_gui.bat           # Windows
bash orcp_gui.sh       # Linux
```

### GPU ASR 高速化（cuDNN 8）

> GPU 音声認識を使用する場合のみ必要です。OCR と CPU ASR は影響を受けません。

ctranslate2 は cuDNN 8 の DLL を必要とします。不足時は ASR が自動的に CPU モードにフォールバックします。

1. https://developer.nvidia.com/cudnn にアクセス（無料登録が必要）
2. **cuDNN 8.9 for CUDA 12.x** をダウンロード
3. DLL を `models/asr/lib/` に配置：
   - **Windows**: `cudnn_ops_infer64_8.dll`, `cudnn_cnn_infer64_8.dll`, `cudnn64_8.dll`
   - **Linux**: `libcudnn_ops_infer.so.8`, `libcudnn_cnn_infer.so.8`, `libcudnn.so.8`

### 初回使用

1. **📂 動画/画像を開く** をクリック、またはファイルをプレビューエリアにドラッグ
2. プレビュー上でドラッグして OCR 領域を描画
3. メニューバー **エンジン(&E)** でエンジンを選択（デフォルト PaddleOCR）
4. **▶ 処理開始** をクリック

## プロジェクト構成

```
orcp/
├── ocr_gui.py                  # アプリケーションエントリポイント
├── pyproject.toml              # プロジェクト設定、依存関係
├── setup.bat / setup.sh        # インストールスクリプト
├── orcp_gui.bat / orcp_gui.sh  # 起動スクリプト
├── diagnose.bat / diagnose.sh  # 診断スクリプト
│
├── config/                     # 設定ファイル (JSON)
├── docs/                       # ドキュメント
│
├── core/                       # コアビジネスロジック
│   ├── llm_utils/              #   統合 LLM ゲートウェイ（再試行、キャッシュ、レート制限）
│   ├── config_manager.py       #   設定の読込/書込/検証
│   ├── ocr_engine.py           #   OCR エンジン
│   ├── asr_engine.py           #   ASR エンジン（サブプロセス分離）
│   ├── workflow_manager.py     #   ワークフロー管理
│   ├── frame_processor.py      #   動画フレームデコード + 字幕検出
│   ├── ai_correction.py        #   LLM 補正 + 文分割 + 校正
│   ├── result_processor.py     #   重複排除、フィルタ、エクスポート
│   └── ...
│
├── ui/                         # PyQt5 ユーザーインターフェース
│   ├── main_window.py          #   メインウィンドウ
│   ├── config_panel.py         #   設定パネル
│   ├── settings_dialog.py      #   詳細設定ダイアログ
│   ├── video_preview.py        #   動画プレビュー + ROI
│   ├── result_table.py         #   結果テーブル + 検索バー
│   ├── collapsible_group.py    #   折りたたみグループウィジェット
│   ├── workers.py              #   QThread ワーカー
│   └── ...
│
├── styles/                     # QSS スタイルシート（ダーク/ライト）
├── tests/                      # ユニットテスト
├── scripts/                    # ユーティリティスクリプト
├── locale/                     # 国際化 (zh_CN / en_US)
└── models/                     # ASR モデル (gitignored)
```

## 開発

```bash
# 開発依存関係のインストール
uv pip install -e ".[dev]"

# リント
ruff check .

# フォーマット
ruff format .

# 型チェック
mypy core/ ui/

# テスト実行
pytest
```

詳細は [CONTRIBUTING.md](CONTRIBUTING.md) を参照。

## ライセンス

[MIT License](LICENSE)
