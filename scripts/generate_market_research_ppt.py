"""
生成 ORCP 跨平台字幕提取工具及同类赛道市场调研报告 PPT
"""

import os
import sys
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

# ── 颜色常量定义 (现代科技深色调 Executive Navy/Tech Palette) ──
BG_DARK = RGBColor(11, 17, 32)        # #0B1120 主背景深黑蓝
CARD_BG = RGBColor(30, 41, 59)        # #1E293B 卡片背景
CARD_BG_LIGHT = RGBColor(51, 65, 85)  # #334155 次级卡片
CARD_BORDER = RGBColor(71, 85, 105)   # #475569 卡片边框

ACCENT_BLUE = RGBColor(37, 99, 235)   # #2563EB 科技蓝
ACCENT_CYAN = RGBColor(56, 189, 248)  # #38BDF8 亮青色 (高亮/数据)
ACCENT_GREEN = RGBColor(16, 185, 129) # #10B981 翡翠绿 (优势/增长)
ACCENT_AMBER = RGBColor(245, 158, 11) # #F59E0B 琥珀橙 (注意/警告)
ACCENT_PURPLE = RGBColor(139, 92, 246)# #8B5CF6 智能紫 (AI/创新)
ACCENT_ROSE = RGBColor(244, 63, 94)   # #F43F5E 玫瑰红 (挑战/劣势)

TEXT_LIGHT = RGBColor(248, 250, 252)  # #F8FAFC 主文本白色
TEXT_MUTED = RGBColor(148, 163, 184)  # #94A3B8 次文本浅灰
TEXT_DIM = RGBColor(100, 116, 139)    # #64748B 辅助文本深灰

FONT_HEADING = "Microsoft YaHei"
FONT_BODY = "Microsoft YaHei"

def create_presentation():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank_slide_layout = prs.slide_layouts[6]

    def add_blank_slide():
        slide = prs.slides.add_slide(blank_slide_layout)
        # 添加全屏深色背景
        bg = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height
        )
        bg.fill.solid()
        bg.fill.fore_color.rgb = BG_DARK
        bg.line.fill.background()
        return slide

    def add_header(slide, tag_text, title_text, subtitle_text=""):
        # 分类标签 Tag
        tag_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.45), Inches(8), Inches(0.35))
        tf_tag = tag_box.text_frame
        tf_tag.word_wrap = True
        tf_tag.margin_left = tf_tag.margin_top = tf_tag.margin_right = tf_tag.margin_bottom = 0
        p_tag = tf_tag.paragraphs[0]
        p_tag.text = f"●  {tag_text.upper()}"
        p_tag.font.name = FONT_BODY
        p_tag.font.size = Pt(11)
        p_tag.font.bold = True
        p_tag.font.color.rgb = ACCENT_CYAN

        # 主标题 Title
        title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.8), Inches(11.5), Inches(0.55))
        tf_title = title_box.text_frame
        tf_title.word_wrap = True
        tf_title.margin_left = tf_title.margin_top = tf_title.margin_right = tf_title.margin_bottom = 0
        p_title = tf_title.paragraphs[0]
        p_title.text = title_text
        p_title.font.name = FONT_HEADING
        p_title.font.size = Pt(22)
        p_title.font.bold = True
        p_title.font.color.rgb = TEXT_LIGHT

        # 副标题 Subtitle
        if subtitle_text:
            sub_box = slide.shapes.add_textbox(Inches(0.8), Inches(1.38), Inches(11.5), Inches(0.4))
            tf_sub = sub_box.text_frame
            tf_sub.word_wrap = True
            tf_sub.margin_left = tf_sub.margin_top = tf_sub.margin_right = tf_sub.margin_bottom = 0
            p_sub = tf_sub.paragraphs[0]
            p_sub.text = subtitle_text
            p_sub.font.name = FONT_BODY
            p_sub.font.size = Pt(12)
            p_sub.font.color.rgb = TEXT_MUTED

    def add_footer(slide, current_idx, total_slides=23):
        # 底部微细分隔线
        line = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(7.0), Inches(11.733), Inches(0.01)
        )
        line.fill.solid()
        line.fill.fore_color.rgb = CARD_BG_LIGHT
        line.line.fill.background()

        # 页脚信息
        box = slide.shapes.add_textbox(Inches(0.8), Inches(7.05), Inches(8), Inches(0.3))
        tf = box.text_frame
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0
        p = tf.paragraphs[0]
        p.text = "ORCP 跨平台字幕提取工具 · 深度市场调研与竞品分析报告 (2026)"
        p.font.name = FONT_BODY
        p.font.size = Pt(9.5)
        p.font.color.rgb = TEXT_DIM

        # 页码
        pbox = slide.shapes.add_textbox(Inches(10.5), Inches(7.05), Inches(2.0), Inches(0.3))
        ptf = pbox.text_frame
        ptf.margin_left = ptf.margin_top = ptf.margin_right = ptf.margin_bottom = 0
        pp = ptf.paragraphs[0]
        pp.alignment = PP_ALIGN.RIGHT
        pp.text = f"{current_idx} / {total_slides}"
        pp.font.name = FONT_BODY
        pp.font.size = Pt(9.5)
        pp.font.color.rgb = TEXT_DIM

    def add_card(slide, left, top, width, height, bg_color=CARD_BG, border_color=CARD_BORDER):
        card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
        card.fill.solid()
        card.fill.fore_color.rgb = bg_color
        card.line.color.rgb = border_color
        card.line.width = Pt(1)
        return card

    # ==========================================
    # SLIDE 1: 封面 / Cover Slide
    # ==========================================
    slide1 = add_blank_slide()

    # 装饰光晕卡片背景
    card_glow = add_card(slide1, Inches(0.8), Inches(0.8), Inches(11.733), Inches(5.9), CARD_BG, RGBColor(30, 58, 138))

    # 顶部标签
    tb = slide1.shapes.add_textbox(Inches(1.4), Inches(1.3), Inches(10), Inches(0.4))
    p = tb.text_frame.paragraphs[0]
    p.text = "EXECUTIVE MARKET RESEARCH & COMPETITIVE ANALYSIS"
    p.font.name = FONT_HEADING
    p.font.size = Pt(12)
    p.font.bold = True
    p.font.color.rgb = ACCENT_CYAN

    # 大标题
    tb = slide1.shapes.add_textbox(Inches(1.4), Inches(1.8), Inches(10.5), Inches(1.5))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "ORCP 跨平台字幕提取工具"
    p.font.name = FONT_HEADING
    p.font.size = Pt(36)
    p.font.bold = True
    p.font.color.rgb = TEXT_LIGHT

    p2 = tf.add_paragraph()
    p2.text = "与视频 AI 字幕赛道深度市场调研报告"
    p2.font.name = FONT_HEADING
    p2.font.size = Pt(28)
    p2.font.bold = True
    p2.font.color.rgb = ACCENT_CYAN

    # 副标题描述
    tb = slide1.shapes.add_textbox(Inches(1.4), Inches(3.6), Inches(10.5), Inches(0.8))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "涵盖全球 OCR/ASR/AI 字幕市场规模、短剧出海与创作者经济驱动力、开源及商业竞品全景对标、ORCP 核心差异化机会与产品落地路线图。"
    p.font.name = FONT_BODY
    p.font.size = Pt(13)
    p.font.color.rgb = TEXT_MUTED

    # 底部 4 大亮点指标徽章
    badges = [
        ("多引擎 OCR 融合", "PaddleOCR + Vision + Ollama", ACCENT_BLUE),
        ("离线语音识别 ASR", "faster-whisper CUDA/CPU", ACCENT_PURPLE),
        ("LLM 思维链后处理", "CoT 语义分句 + 纠错 + 翻译", ACCENT_GREEN),
        ("本地优先 · 隐私合规", "100% 离线可用 · 零按量计费", ACCENT_CYAN),
    ]
    badge_w = Inches(2.55)
    for i, (b_title, b_desc, b_col) in enumerate(badges):
        bx = Inches(1.4) + i * (badge_w + Inches(0.24))
        by = Inches(4.7)
        add_card(slide1, bx, by, badge_w, Inches(1.4), CARD_BG_LIGHT, b_col)
        tb = slide1.shapes.add_textbox(bx + Inches(0.15), by + Inches(0.18), badge_w - Inches(0.3), Inches(1.0))
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = b_title
        p.font.name = FONT_HEADING
        p.font.size = Pt(12)
        p.font.bold = True
        p.font.color.rgb = b_col
        p2 = tf.add_paragraph()
        p2.text = b_desc
        p2.font.name = FONT_BODY
        p2.font.size = Pt(10)
        p2.font.color.rgb = TEXT_LIGHT

    # 底部报告信息
    tb = slide1.shapes.add_textbox(Inches(1.4), Inches(6.25), Inches(10), Inches(0.35))
    p = tb.text_frame.paragraphs[0]
    p.text = "产品规划与战略参考 · 2026 年 8 月 · 本地优先多模态字幕工作台"
    p.font.name = FONT_BODY
    p.font.size = Pt(10.5)
    p.font.color.rgb = TEXT_DIM

    # ==========================================
    # SLIDE 2: 执行摘要 / Executive Summary
    # ==========================================
    slide2 = add_blank_slide()
    add_header(slide2, "Executive Summary", "执行摘要：市场洞察与核心研判", "一站式把握赛道趋势、市场红利、竞争痛点与 ORCP 破局点")
    add_footer(slide2, 2)

    cards_data = [
        ("01 · 市场高景气度", "宏观与垂直赛道爆发", [
            "全球 OCR 市场规模从 2023 年 105 亿美元稳健增长至 2030 年 304 亿美元（CAGR 16.2%）。",
            "全球 AI 字幕生成器市场 2025 年 2.67 亿美元，2032 年达 5.75 亿美元，中国市场增速显著领跑全球（占比超 30%）。",
            "短剧出海与短视频全球化成为最强需求放大器，海外短剧突破 50 亿美元规模，92% 跨境团队将 AI 字幕列为刚需。"
        ], ACCENT_CYAN),
        ("02 · 竞品生态断层", "开源割裂 vs 商业昂贵", [
            "开源竞品割裂：VSE 仅能做硬字幕 OCR；faster-whisper/WhisperX 仅能做 ASR；Subtitle Edit 界面老旧无 LLM 流水线。",
            "商业产品昂贵封闭：剪映/CapCut 无法提取第三方硬字幕；通义听悟/Descript 按量收费且强制数据上云。",
            "目前全球范围内缺乏同时整合「视频硬字幕 OCR + 本地 ASR + LLM 语义后处理」的现代化桌面工具。"
        ], ACCENT_PURPLE),
        ("03 · ORCP 核心壁垒", "双模态融合与本地优先", [
            "双模态一体：行业唯一打通「画面 ROI 硬字幕识别 + 音频语音转写」的开源桌面工作台。",
            "大模型 CoT 赋能：独创思维链语义分句、多区域独立配置、智能纠错与翻译，彻底解决 OCR 碎片化断句顽疾。",
            "本地优先（Local-First）：100% 数据离线可用，零按量计费，契合内容创作者与企业隐私合规要求。"
        ], ACCENT_GREEN),
        ("04 · 落地演进路线", "对齐标准与生态突围", [
            "短期对齐基准：推出一键安装包/免配置运行时，集成 WhisperX 词级时间戳与说话人分离，支持剪辑软件工程导出。",
            "中期功能闭环：整合 VSR 去字幕修复，构建「提取 -> 擦除 -> 翻译 -> 烧录」全流程闭环工作流。",
            "长期商业路径：开源核心聚拢社区口碑，探索企业私有化部署、短剧出海定制工具箱与专业版增值服务。"
        ], ACCENT_AMBER),
    ]

    card_w = Inches(5.72)
    card_h = Inches(2.45)
    coords = [
        (Inches(0.8), Inches(1.85)),
        (Inches(6.8), Inches(1.85)),
        (Inches(0.8), Inches(4.45)),
        (Inches(6.8), Inches(4.45)),
    ]

    for i, (tag, title, bullets, col) in enumerate(cards_data):
        cx, cy = coords[i]
        add_card(slide2, cx, cy, card_w, card_h, CARD_BG, col)

        tb = slide2.shapes.add_textbox(cx + Inches(0.25), cy + Inches(0.2), card_w - Inches(0.5), card_h - Inches(0.4))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = f"{tag}  |  {title}"
        p.font.name = FONT_HEADING
        p.font.size = Pt(13.5)
        p.font.bold = True
        p.font.color.rgb = col

        for bullet in bullets:
            pb = tf.add_paragraph()
            pb.text = f"•  {bullet}"
            pb.font.name = FONT_BODY
            pb.font.size = Pt(10.2)
            pb.font.color.rgb = TEXT_LIGHT
            pb.space_before = Pt(4)

    # ==========================================
    # SLIDE 3: 目录 / Table of Contents
    # ==========================================
    slide3 = add_blank_slide()
    add_header(slide3, "Agenda", "调研报告目录 / CONTENTS", "五个核心维度全面剖析 ORCP 项目及行业发展格局")
    add_footer(slide3, 3)

    chapters = [
        ("01", "项目定位与全景解构", "ORCP 是什么、技术架构体系、核心功能与目标用户画像", ACCENT_BLUE),
        ("02", "宏观市场与需求驱动", "OCR / ASR / AI 字幕市场规模，短剧出海与创作者经济爆发", ACCENT_CYAN),
        ("03", "开源与商业竞品对标", "GitHub 开源工具生态矩阵、能力横向对比表、云端 SaaS 优劣势", ACCENT_PURPLE),
        ("04", "SWOT 与核心差异化机会", "ORCP 核心优势、劣势分析、独特价值主张 (UVP) 与破局点", ACCENT_GREEN),
        ("05", "战略规划与落地行动建议", "产品三阶段演进路线图、商业化探索与立即执行清单", ACCENT_AMBER),
    ]

    c_w = Inches(11.733)
    c_h = Inches(0.88)
    for i, (num, c_title, c_desc, c_col) in enumerate(chapters):
        cy = Inches(1.9) + i * (c_h + Inches(0.14))
        add_card(slide3, Inches(0.8), cy, c_w, c_h, CARD_BG, c_col)

        # 序号
        tb_num = slide3.shapes.add_textbox(Inches(1.1), cy + Inches(0.18), Inches(0.8), Inches(0.5))
        p_num = tb_num.text_frame.paragraphs[0]
        p_num.text = num
        p_num.font.name = FONT_HEADING
        p_num.font.size = Pt(24)
        p_num.font.bold = True
        p_num.font.color.rgb = c_col

        # 内容
        tb_text = slide3.shapes.add_textbox(Inches(2.0), cy + Inches(0.18), Inches(10.2), Inches(0.55))
        tf_text = tb_text.text_frame
        tf_text.word_wrap = True
        tf_text.margin_left = tf_text.margin_top = tf_text.margin_right = tf_text.margin_bottom = 0
        p_t = tf_text.paragraphs[0]
        p_t.text = c_title
        p_t.font.name = FONT_HEADING
        p_t.font.size = Pt(14)
        p_t.font.bold = True
        p_t.font.color.rgb = TEXT_LIGHT

        p_d = tf_text.add_paragraph()
        p_d.text = c_desc
        p_d.font.name = FONT_BODY
        p_d.font.size = Pt(10.5)
        p_d.font.color.rgb = TEXT_MUTED

    # ==========================================
    # SLIDE 4: 第一篇章过渡页 / Chapter 1 Divider
    # ==========================================
    slide4 = add_blank_slide()
    add_card(slide4, Inches(0.8), Inches(0.8), Inches(11.733), Inches(5.9), CARD_BG, ACCENT_BLUE)

    tb = slide4.shapes.add_textbox(Inches(1.5), Inches(2.2), Inches(10), Inches(3.0))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "CHAPTER 01"
    p.font.name = FONT_HEADING
    p.font.size = Pt(16)
    p.font.bold = True
    p.font.color.rgb = ACCENT_CYAN

    p2 = tf.add_paragraph()
    p2.text = "项目定位与全景解构"
    p2.font.name = FONT_HEADING
    p2.font.size = Pt(32)
    p2.font.bold = True
    p2.font.color.rgb = TEXT_LIGHT
    p2.space_before = Pt(10)

    p3 = tf.add_paragraph()
    p3.text = "深入剖析 ORCP 的产品定位、技术架构特性、核心功能模块与典型用户应用场景。"
    p3.font.name = FONT_BODY
    p3.font.size = Pt(14)
    p3.font.color.rgb = TEXT_MUTED
    p3.space_before = Pt(15)

    add_footer(slide4, 4)

    # ==========================================
    # SLIDE 5: ORCP 技术架构与功能矩阵
    # ==========================================
    slide5 = add_blank_slide()
    add_header(slide5, "Project Overview", "ORCP 项目架构与核心能力矩阵", "本地优先 + 双模态提取 + LLM 智能增强的现代化桌面字幕工作台")
    add_footer(slide5, 5)

    features = [
        ("多引擎 OCR 体系", ACCENT_BLUE, [
            "PaddleOCR：本地 GPU/CPU 加速，离线高准确率",
            "OpenAI Vision：云端多模态大模型视觉识别",
            "Ollama / LlamaCpp：本地私有大模型 Vision 接口",
            "多区域独立配置：不同 ROI 支持指定独立引擎"
        ]),
        ("离线语音识别 (ASR)", ACCENT_PURPLE, [
            "faster-whisper：基于 CTranslate2 极速推理",
            "子进程隔离：独立环境与 CUDA 隔离，稳定防崩溃",
            "cuDNN 8 加速：自动检测 GPU，无环境自动回退 CPU",
            "VAD 静音过滤：准确切分有效语音片段"
        ]),
        ("LLM 智能后处理", ACCENT_GREEN, [
            "CoT 思维链分句：将碎片化 OCR 文本重构为完整字幕",
            "智能纠错与翻译：多预设切换（DeepSeek/OpenAI等）",
            "二次校对模式：语法修正、术语对齐与质量复检",
            "统一网关：指数退避重试 + 滑动窗口限速 + 缓存"
        ]),
        ("视频帧处理与 UI 工作流", ACCENT_CYAN, [
            "哨兵检测机制：字数骤降比 + 相似度动态去重",
            "ROI 交互：视频预览拖拽绘制框选区域与膨胀裁剪",
            "多格式导出：一键输出 SRT, TXT, JSON, CSV",
            "PySide6 现代 UI：明暗主题、结果表格搜索过滤"
        ]),
    ]

    card_w = Inches(2.78)
    card_h = Inches(4.7)
    for i, (title, col, items) in enumerate(features):
        cx = Inches(0.8) + i * (card_w + Inches(0.2))
        cy = Inches(1.9)
        add_card(slide5, cx, cy, card_w, card_h, CARD_BG, col)

        tb = slide5.shapes.add_textbox(cx + Inches(0.18), cy + Inches(0.2), card_w - Inches(0.36), card_h - Inches(0.4))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_HEADING
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = col

        for item in items:
            pb = tf.add_paragraph()
            pb.text = f"• {item}"
            pb.font.name = FONT_BODY
            pb.font.size = Pt(10.0)
            pb.font.color.rgb = TEXT_LIGHT
            pb.space_before = Pt(8)

    # ==========================================
    # SLIDE 6: 目标用户画像与核心使用场景
    # ==========================================
    slide6 = add_blank_slide()
    add_header(slide6, "Target Audience", "目标用户画像与四大核心业务场景", "精准覆盖出海短剧、自媒体二创、专业字幕组及企业知识库归档")
    add_footer(slide6, 6)

    personas = [
        ("短剧 / 影视出海团队", "痛点：硬字幕压制无法编辑、跨语种翻译昂贵、交期紧迫", [
            "场景：从已压制中文字幕的短剧素材中逆向提取原台词。",
            "价值：结合 LLM 一键翻译并生成多语种 SRT，单剧译制成本下降 85%+，出海周期缩短至小时级。"
        ], ACCENT_CYAN),
        ("短视频创作者 / 二创搬运", "痛点：搬运外网视频需二次汉化、手工听写耗费大量精力", [
            "场景：提取 TikTok、YouTube 原视频字幕或解说音频直接转写。",
            "价值：哨兵流式检测自动过滤重复帧，AI 智能分句使字幕段落自然流畅，大幅提升二创出片效率。"
        ], ACCENT_PURPLE),
        ("字幕组 / 影视译者", "痛点：生肉视频无字幕源文件、人名专有名词翻译不准", [
            "场景：处理无外挂字幕的生肉影视剧，提取硬字幕作为打轴校对底稿。",
            "价值：CoT 语义纠错支持自定义术语提示词模板，二次校对保障专业译制水准。"
        ], ACCENT_GREEN),
        ("企业培训 / 知识库整理", "痛点：海量内训、会议、课程视频难以检索，涉及商业隐私", [
            "场景：批量将录屏 PPT 字幕及演讲录音提取为结构化文本与知识图谱。",
            "价值：全本地离线运行，企业核心数据绝不上云，零泄漏风险且无按量计费负担。"
        ], ACCENT_AMBER),
    ]

    card_w = Inches(5.72)
    card_h = Inches(2.35)
    coords = [
        (Inches(0.8), Inches(1.9)),
        (Inches(6.8), Inches(1.9)),
        (Inches(0.8), Inches(4.45)),
        (Inches(6.8), Inches(4.45)),
    ]

    for i, (title, pain, points, col) in enumerate(personas):
        cx, cy = coords[i]
        add_card(slide6, cx, cy, card_w, card_h, CARD_BG, col)

        tb = slide6.shapes.add_textbox(cx + Inches(0.22), cy + Inches(0.18), card_w - Inches(0.44), card_h - Inches(0.36))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_HEADING
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = col

        p_pain = tf.add_paragraph()
        p_pain.text = pain
        p_pain.font.name = FONT_BODY
        p_pain.font.size = Pt(9.8)
        p_pain.font.color.rgb = TEXT_MUTED
        p_pain.space_before = Pt(2)

        for pt_text in points:
            pb = tf.add_paragraph()
            pb.text = f"• {pt_text}"
            pb.font.name = FONT_BODY
            pb.font.size = Pt(10.0)
            pb.font.color.rgb = TEXT_LIGHT
            pb.space_before = Pt(4)

    # ==========================================
    # SLIDE 7: 第二篇章过渡页 / Chapter 2 Divider
    # ==========================================
    slide7 = add_blank_slide()
    add_card(slide7, Inches(0.8), Inches(0.8), Inches(11.733), Inches(5.9), CARD_BG, ACCENT_CYAN)

    tb = slide7.shapes.add_textbox(Inches(1.5), Inches(2.2), Inches(10), Inches(3.0))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "CHAPTER 02"
    p.font.name = FONT_HEADING
    p.font.size = Pt(16)
    p.font.bold = True
    p.font.color.rgb = ACCENT_CYAN

    p2 = tf.add_paragraph()
    p2.text = "宏观市场与需求驱动"
    p2.font.name = FONT_HEADING
    p2.font.size = Pt(32)
    p2.font.bold = True
    p2.font.color.rgb = TEXT_LIGHT
    p2.space_before = Pt(10)

    p3 = tf.add_paragraph()
    p3.text = "全球 OCR / ASR / AI 字幕生成市场规模测算，短剧出海与全球创作者经济的爆发驱动力。"
    p3.font.name = FONT_BODY
    p3.font.size = Pt(14)
    p3.font.color.rgb = TEXT_MUTED
    p3.space_before = Pt(15)

    add_footer(slide7, 7)

    # ==========================================
    # SLIDE 8: 全球关联市场大盘数据
    # ==========================================
    slide8 = add_blank_slide()
    add_header(slide8, "Market Sizing", "宏观市场大盘：四大关联市场均处高速增长期", "从 OCR 基础底座到 AI 字幕垂直应用，市场复合增长率均超两位数")
    add_footer(slide8, 8)

    stats = [
        ("全球 OCR 市场规模", "105 → 304", "亿美元", "2023 - 2030E · CAGR 16.2%", "Grand View Research 数据。计算机视觉及大模型多模态能力推动 OCR 从文档数字化向视频/跨模态识别全面升级。", ACCENT_CYAN),
        ("全球 AI 字幕生成器", "2.67 → 5.75", "亿美元", "2025 - 2032E · CAGR 11.6%", "QYResearch 行业报告。中国市场增速最快（占 30%+），影视制作（37.6%）与短视频创作（28.9%）为主要支撑。", ACCENT_PURPLE),
        ("全球微短剧出海市场", "40 → 50+", "亿美元", "2025 - 2026E · 爆发式扩张", "短剧出海规模已破 350 亿人民币，中国厂商贡献超 45%。AI 自动化译制成为各大出海平台降本增效核心生死线。", ACCENT_GREEN),
        ("全球智能语音转写 (ASR)", "41.3 → 100", "亿美元", "2024 - 2029E · CAGR 19.3%", "以 Whisper、Conformer 为代表的开源与商业 ASR 模型普及，音视频转文字准确率在常规环境下已突破 96%。", ACCENT_AMBER),
    ]

    card_w = Inches(2.78)
    card_h = Inches(4.7)
    for i, (title, num, unit, cagr, desc, col) in enumerate(stats):
        cx = Inches(0.8) + i * (card_w + Inches(0.2))
        cy = Inches(1.9)
        add_card(slide8, cx, cy, card_w, card_h, CARD_BG, col)

        tb = slide8.shapes.add_textbox(cx + Inches(0.18), cy + Inches(0.2), card_w - Inches(0.36), card_h - Inches(0.4))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_HEADING
        p.font.size = Pt(11.5)
        p.font.bold = True
        p.font.color.rgb = TEXT_MUTED

        p_num = tf.add_paragraph()
        p_num.text = num
        p_num.font.name = FONT_HEADING
        p_num.font.size = Pt(24)
        p_num.font.bold = True
        p_num.font.color.rgb = col
        p_num.space_before = Pt(6)

        p_u = tf.add_paragraph()
        p_u.text = unit
        p_u.font.name = FONT_BODY
        p_u.font.size = Pt(11)
        p_u.font.bold = True
        p_u.font.color.rgb = col

        p_cagr = tf.add_paragraph()
        p_cagr.text = cagr
        p_cagr.font.name = FONT_BODY
        p_cagr.font.size = Pt(9.5)
        p_cagr.font.color.rgb = ACCENT_CYAN
        p_cagr.space_before = Pt(6)

        p_desc = tf.add_paragraph()
        p_desc.text = desc
        p_desc.font.name = FONT_BODY
        p_desc.font.size = Pt(9.8)
        p_desc.font.color.rgb = TEXT_LIGHT
        p_desc.space_before = Pt(10)

    # ==========================================
    # SLIDE 9: 核心驱动力一：短剧出海与内容工业化
    # ==========================================
    slide9 = add_blank_slide()
    add_header(slide9, "Growth Driver 1", "核心驱动力一：短剧出海爆发与 AI 译制降本革命", "短剧出海从「买量红利」进入「工业化精细运营」，AI 字幕是降本第一刀")
    add_footer(slide9, 9)

    add_card(slide9, Inches(0.8), Inches(1.9), Inches(5.72), Inches(4.7), CARD_BG, ACCENT_CYAN)
    tb = slide9.shapes.add_textbox(Inches(1.05), Inches(2.1), Inches(5.2), Inches(4.3))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

    p = tf.paragraphs[0]
    p.text = "短剧出海重塑视频本地化产业链"
    p.font.name = FONT_HEADING
    p.font.size = Pt(14)
    p.font.bold = True
    p.font.color.rgb = ACCENT_CYAN

    p_body = [
        "爆发式增长：2025 年国内短剧规模达 500 亿，海外微短剧规模突破 50 亿美元，中国出海短剧 App 数量超 100 款。",
        "痛点显著——增收不增利：投流成本占比高达 70%-80%，译制成本与产出周期成为决定单剧能否跑通盈利模型的关键瓶颈。",
        "硬字幕逆向提取成为刚需：大量国内成品短剧原始工程已不可考，仅有带硬字幕的压制视频，必须通过 OCR 逆向提取原文字幕。",
        "百倍效率跃迁：传统人工译制一部 100 集短剧需 3-5 天、成本 2000-3000 元；AI 自动化管线将成本压降至 300-500 元，耗时缩短至数小时。"
    ]
    for pb_text in p_body:
        p_item = tf.add_paragraph()
        p_item.text = f"• {pb_text}"
        p_item.font.name = FONT_BODY
        p_item.font.size = Pt(10.2)
        p_item.font.color.rgb = TEXT_LIGHT
        p_item.space_before = Pt(8)

    add_card(slide9, Inches(6.8), Inches(1.9), Inches(5.72), Inches(4.7), CARD_BG, ACCENT_PURPLE)
    tb2 = slide9.shapes.add_textbox(Inches(7.05), Inches(2.1), Inches(5.2), Inches(4.3))
    tf2 = tb2.text_frame
    tf2.word_wrap = True
    tf2.margin_left = tf2.margin_top = tf2.margin_right = tf2.margin_bottom = 0

    p = tf2.paragraphs[0]
    p.text = "短剧出海译制需求的三重演进"
    p.font.name = FONT_HEADING
    p.font.size = Pt(14)
    p.font.bold = True
    p.font.color.rgb = ACCENT_PURPLE

    stages = [
        ("阶段一：机械直译 (已淘汰)", "早期的字对字 OCR+机翻，产生大量语义混乱、断句生硬、文化梗失真，完播率极低。"),
        ("阶段二：CoT 语义重构 (当前主流)", "利用大模型思维链理解全剧上下文，对 OCR 碎词进行断句重组与俚语本土化修正（ORCP 核心发力点）。"),
        ("阶段三：多模态端到端工坊 (未来方向)", "提取硬字幕 + 视频画面抹除原字幕 + 多语种配音克隆 + 动态重烧录的一体化全自动化工坊。")
    ]
    for s_title, s_desc in stages:
        ps_t = tf2.add_paragraph()
        ps_t.text = s_title
        ps_t.font.name = FONT_HEADING
        ps_t.font.size = Pt(11)
        ps_t.font.bold = True
        ps_t.font.color.rgb = TEXT_LIGHT
        ps_t.space_before = Pt(10)

        ps_d = tf2.add_paragraph()
        ps_d.text = s_desc
        ps_d.font.name = FONT_BODY
        ps_d.font.size = Pt(9.8)
        ps_d.font.color.rgb = TEXT_MUTED
        ps_d.space_before = Pt(2)

    # ==========================================
    # SLIDE 10: 核心驱动力二：创作者经济与隐私合规
    # ==========================================
    slide10 = add_blank_slide()
    add_header(slide10, "Growth Driver 2", "核心驱动力二：创作者经济与数据隐私本地化", "自媒体二创爆发催生海量需求，数据资产安全倒逼工具走向 Local-First")
    add_footer(slide10, 10)

    cards_d2 = [
        ("创作者二创与跨境电商刚需", ACCENT_CYAN, [
            "全球内容生产井喷：仅中国创作者账号就超 16.2 亿，日均视频产量超 1.3 亿条；TikTok / YouTube Shorts / Reels 催生海量跨国搬运与二创。",
            "92% 跨境创作者核心需求：多语言视频字幕提取与翻译被列为最刚需生产力工具。",
            "痛点：传统人工听写耗费 80% 剪辑时间，字幕打轴繁琐，急需自动化工具实现「即导即出」。"
        ]),
        ("云端 SaaS 的成本黑洞", ACCENT_AMBER, [
            "高昂的订阅费与按量收费：Descript, Notta 等海外 SaaS 每月 20-50 美元，且普遍限制分钟数与视频文件大小。",
            "国内云端转写限制：按时长计费，长视频与批量处理成本迅速攀升，高频使用下开发者与工作室难以承受。",
            "网络依赖与排队延迟：大文件上传消耗带宽，遇到网络波动或服务器排队效率骤降。"
        ]),
        ("本地优先 (Local-First) 与零信任隐私", ACCENT_GREEN, [
            "核心资产不出设备：未公开影视片源、企业内部培训、商业会议录音具有极高保密性，严禁上传第三方公有云。",
            "算力平民化红利：消费级显卡（RTX 3060/4060）与 NPU 普及，本地运行 PaddleOCR 与 faster-whisper 已可实现毫秒级响应。",
            "ORCP 契合合规诉求：100% 离线运行，免除数据泄露风险，零边际成本无限次使用。"
        ]),
    ]

    card_w = Inches(3.72)
    card_h = Inches(4.7)
    for i, (title, col, items) in enumerate(cards_d2):
        cx = Inches(0.8) + i * (card_w + Inches(0.28))
        cy = Inches(1.9)
        add_card(slide10, cx, cy, card_w, card_h, CARD_BG, col)

        tb = slide10.shapes.add_textbox(cx + Inches(0.2), cy + Inches(0.2), card_w - Inches(0.4), card_h - Inches(0.4))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_HEADING
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = col

        for item in items:
            pb = tf.add_paragraph()
            pb.text = f"• {item}"
            pb.font.name = FONT_BODY
            pb.font.size = Pt(9.8)
            pb.font.color.rgb = TEXT_LIGHT
            pb.space_before = Pt(8)

    # ==========================================
    # SLIDE 11: 第三篇章过渡页 / Chapter 3 Divider
    # ==========================================
    slide11 = add_blank_slide()
    add_card(slide11, Inches(0.8), Inches(0.8), Inches(11.733), Inches(5.9), CARD_BG, ACCENT_PURPLE)

    tb = slide11.shapes.add_textbox(Inches(1.5), Inches(2.2), Inches(10), Inches(3.0))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "CHAPTER 03"
    p.font.name = FONT_HEADING
    p.font.size = Pt(16)
    p.font.bold = True
    p.font.color.rgb = ACCENT_CYAN

    p2 = tf.add_paragraph()
    p2.text = "开源与商业竞品全景对标"
    p2.font.name = FONT_HEADING
    p2.font.size = Pt(32)
    p2.font.bold = True
    p2.font.color.rgb = TEXT_LIGHT
    p2.space_before = Pt(10)

    p3 = tf.add_paragraph()
    p3.text = "GitHub 开源工具生态格局、核心能力多维矩阵对比、商业 SaaS 模式与差异化窗口。"
    p3.font.name = FONT_BODY
    p3.font.size = Pt(14)
    p3.font.color.rgb = TEXT_MUTED
    p3.space_before = Pt(15)

    add_footer(slide11, 11)

    # ==========================================
    # SLIDE 12: GitHub 开源竞品生态全景
    # ==========================================
    slide12 = add_blank_slide()
    add_header(slide12, "Open Source Landscape", "开源生态全景：底层基础库与上层应用工具格局", "ORCP 位于「多引擎底座 + 桌面端综合工作流」的黄金交汇点")
    add_footer(slide12, 12)

    categories = [
        ("OCR / 视觉基础底座", [
            ("PaddleOCR", "88k+ Stars", "百度开源，工业级中英文识别标杆，ORCP 默认本地 OCR 引擎"),
            ("Tesseract", "76k+ Stars", "Google 开源老牌 OCR 引擎，多语言支持完备但中文弱于 Paddle"),
            ("RapidOCR", "7.5k+ Stars", "基于 ONNXRuntime 的轻量化跨平台推理库，启动快、体积小")
        ], ACCENT_BLUE),
        ("ASR / 语音识别底座", [
            ("faster-whisper", "25k+ Stars", "CTranslate2 重构的高效 Whisper，推理速度提升 4x，ORCP 已集成"),
            ("WhisperX", "23.6k+ Stars", "基于 wav2vec2 音素对齐实现词级时间戳 + 说话人分离，行业基准"),
            ("OpenAI Whisper", "75k+ Stars", "语音转写通用基础大模型，多语种泛化能力极强")
        ], ACCENT_PURPLE),
        ("视频字幕垂直应用", [
            ("video-subtitle-extractor", "9.4k+ Stars", "国内硬字幕提取经典项目，专精 OCR 提取，无 ASR 和 LLM"),
            ("Video-Subtitle-Remover", "12.4k+ Stars", "视频硬字幕擦除与 AI 画面修补，与 ORCP 形成完美互补"),
            ("AutoSubs / Subtitle Edit", "4k / 14k Stars", "AutoSubs 联动达芬奇剪辑；Subtitle Edit 老牌打轴但无 LLM 流水线")
        ], ACCENT_GREEN),
    ]

    card_w = Inches(3.72)
    card_h = Inches(4.7)
    for i, (cat_title, projects, col) in enumerate(categories):
        cx = Inches(0.8) + i * (card_w + Inches(0.28))
        cy = Inches(1.9)
        add_card(slide12, cx, cy, card_w, card_h, CARD_BG, col)

        tb = slide12.shapes.add_textbox(cx + Inches(0.2), cy + Inches(0.2), card_w - Inches(0.4), card_h - Inches(0.4))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = cat_title
        p.font.name = FONT_HEADING
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = col

        for pname, pstar, pdesc in projects:
            p_n = tf.add_paragraph()
            p_n.text = f"{pname}  ({pstar})"
            p_n.font.name = FONT_HEADING
            p_n.font.size = Pt(11)
            p_n.font.bold = True
            p_n.font.color.rgb = TEXT_LIGHT
            p_n.space_before = Pt(8)

            p_d = tf.add_paragraph()
            p_d.text = pdesc
            p_d.font.name = FONT_BODY
            p_d.font.size = Pt(9.5)
            p_d.font.color.rgb = TEXT_MUTED
            p_d.space_before = Pt(2)

    # ==========================================
    # SLIDE 13: 竞品能力多维矩阵横向对比表
    # ==========================================
    slide13 = add_blank_slide()
    add_header(slide13, "Competitor Matrix", "核心竞品能力多维矩阵横向对标", "ORCP 是全球唯一融合「硬字幕 OCR + 离线 ASR + LLM 语义后处理」的开源桌面工具")
    add_footer(slide13, 13)

    # 创建表格
    rows = 7
    cols = 7
    left = Inches(0.8)
    top = Inches(1.9)
    width = Inches(11.733)
    height = Inches(4.7)

    table_shape = slide13.shapes.add_table(rows, cols, left, top, width, height)
    table = table_shape.table

    # 列宽分配
    table.columns[0].width = Inches(2.1)
    table.columns[1].width = Inches(1.6)
    table.columns[2].width = Inches(1.6)
    table.columns[3].width = Inches(1.6)
    table.columns[4].width = Inches(1.6)
    table.columns[5].width = Inches(1.6)
    table.columns[6].width = Inches(1.6)

    table_data = [
        ["产品 / 工具名称", "硬字幕 OCR", "语音转写 ASR", "LLM 语义分句", "AI 纠错翻译", "本地离线可用", "现代化 GUI"],
        ["ORCP (本项目)", "✓ 多引擎", "✓ faster-whisper", "✓ CoT 语义重构", "✓ 统一网关", "✓ 100% 离线", "✓ PySide6 现代"],
        ["VSE (字幕提取器)", "✓ PaddleOCR", "✕ 无此功能", "✕ 无此功能", "✕ 无此功能", "✓ 本地运行", "△ 基础 Web/PyQt"],
        ["WhisperX", "✕ 无此功能", "✓ 词级对齐/分离", "✕ 无", "✕ 无", "✓ 本地 CLI", "✕ 仅 CLI 脚本"],
        ["Subtitle Edit", "△ 需配置插件", "△ 需外挂模型", "✕ 无原生 LLM", "△ 简单规则", "✓ 本地运行", "△ WinForms 老旧"],
        ["AutoSubs (达芬奇)", "✕ 无此功能", "✓ 结合 Whisper", "✕ 无", "✕ 无", "✓ 本地插件", "✓ 剪辑宿主插件"],
        ["剪映 / CapCut", "✕ 无硬字幕提取", "✓ 云端 ASR", "△ 简单断句", "✓ 云端翻译", "✕ 依赖云端", "✓ 商业套件"],
    ]

    for r_idx, row in enumerate(table_data):
        for c_idx, cell_value in enumerate(row):
            cell = table.cell(r_idx, c_idx)
            cell.text = cell_value
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = cell.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER if c_idx > 0 else PP_ALIGN.LEFT
            p.font.name = FONT_BODY
            p.font.size = Pt(10)

            # 样式设置
            if r_idx == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = CARD_BG_LIGHT
                p.font.bold = True
                p.font.color.rgb = ACCENT_CYAN
            elif r_idx == 1:
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor(23, 37, 84) # 强调行深蓝
                p.font.bold = True
                p.font.color.rgb = ACCENT_GREEN if "✓" in cell_value else TEXT_LIGHT
            else:
                cell.fill.solid()
                cell.fill.fore_color.rgb = CARD_BG
                if "✓" in cell_value:
                    p.font.color.rgb = ACCENT_GREEN
                elif "✕" in cell_value:
                    p.font.color.rgb = ACCENT_ROSE
                elif "△" in cell_value:
                    p.font.color.rgb = ACCENT_AMBER
                else:
                    p.font.color.rgb = TEXT_LIGHT

    # ==========================================
    # SLIDE 14: 商业 / 云端 SaaS 竞品深度分析
    # ==========================================
    slide14 = add_blank_slide()
    add_header(slide14, "Commercial Competitors", "商业及云端产品对标：SaaS 模式痛点与差异化窗口", "商业工具虽功能完善但存在「隐私外泄、按量扣费、无法提取硬字幕」三大天然缺陷")
    add_footer(slide14, 14)

    comm_cards = [
        ("剪映 / CapCut (字节跳动)", "行业统治级视频剪辑工具", [
            "优势：内置自动字幕打轴、AI 翻译与智能配音体验极佳，生态完备。",
            "致命缺陷：完全不支持对已有视频中的「硬编码硬字幕」进行 OCR 识别逆向还原。",
            "收费趋势：高级 AI 翻译与识别逐步推行 VIP 订阅与算力积分制。"
        ], ACCENT_CYAN),
        ("通义听悟 / 讯飞听见 (阿里/讯飞)", "国内会议与音视频转写标杆", [
            "优势：中文口音识别及领域专业术语识别率行业顶尖，集成大模型摘要总结。",
            "致命缺陷：仅支持音频 ASR 转写，无画面 OCR 功能；视频必须上传公有云。",
            "收费模式：按分钟计费（约 0.3-0.5 元/分钟），长视频批量成本高昂。"
        ], ACCENT_PURPLE),
        ("Descript / Veed.io (海外 SaaS)", "海外头部 AI 视频与字幕编辑器", [
            "优势：基于文本直接剪辑视频、AI 配音克隆与自动字幕动效领先全球。",
            "致命缺陷：海外服务器网络访问受限、价格极其昂贵（$24-$50/人/月）。",
            "数据隐私风险：跨境数据传输不符合国内企业合规与保密审查。"
        ], ACCENT_AMBER),
    ]

    card_w = Inches(3.72)
    card_h = Inches(4.7)
    for i, (title, subtitle, items, col) in enumerate(comm_cards):
        cx = Inches(0.8) + i * (card_w + Inches(0.28))
        cy = Inches(1.9)
        add_card(slide14, cx, cy, card_w, card_h, CARD_BG, col)

        tb = slide14.shapes.add_textbox(cx + Inches(0.2), cy + Inches(0.2), card_w - Inches(0.4), card_h - Inches(0.4))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_HEADING
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = col

        p_sub = tf.add_paragraph()
        p_sub.text = subtitle
        p_sub.font.name = FONT_BODY
        p_sub.font.size = Pt(9.5)
        p_sub.font.color.rgb = TEXT_MUTED
        p_sub.space_before = Pt(2)

        for item in items:
            pb = tf.add_paragraph()
            pb.text = f"• {item}"
            pb.font.name = FONT_BODY
            pb.font.size = Pt(9.8)
            pb.font.color.rgb = TEXT_LIGHT
            pb.space_before = Pt(8)

    # ==========================================
    # SLIDE 15: 第四篇章过渡页 / Chapter 4 Divider
    # ==========================================
    slide15 = add_blank_slide()
    add_card(slide15, Inches(0.8), Inches(0.8), Inches(11.733), Inches(5.9), CARD_BG, ACCENT_GREEN)

    tb = slide15.shapes.add_textbox(Inches(1.5), Inches(2.2), Inches(10), Inches(3.0))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "CHAPTER 04"
    p.font.name = FONT_HEADING
    p.font.size = Pt(16)
    p.font.bold = True
    p.font.color.rgb = ACCENT_CYAN

    p2 = tf.add_paragraph()
    p2.text = "SWOT 分析与核心差异化机会"
    p2.font.name = FONT_HEADING
    p2.font.size = Pt(32)
    p2.font.bold = True
    p2.font.color.rgb = TEXT_LIGHT
    p2.space_before = Pt(10)

    p3 = tf.add_paragraph()
    p3.text = "全方位评估 ORCP 的战略态势，挖掘不可替代的核心差异化壁垒 (UVP) 与需要补齐的短板。"
    p3.font.name = FONT_BODY
    p3.font.size = Pt(14)
    p3.font.color.rgb = TEXT_MUTED
    p3.space_before = Pt(15)

    add_footer(slide15, 15)

    # ==========================================
    # SLIDE 16: ORCP SWOT 战略态势分析
    # ==========================================
    slide16 = add_blank_slide()
    add_header(slide16, "SWOT Analysis", "ORCP 战略态势分析 (SWOT Matrix)", "客观审视内部优劣势与外部机会威胁，明确攻防策略")
    add_footer(slide16, 16)

    swot_items = [
        ("优势 Strengths (内部积极)", [
            "双模态融合：同时支持硬字幕 OCR 与音频 ASR 转写。",
            "AI 思维链后处理：独创 CoT 语义分句与纠错，段落连贯度高。",
            "多引擎插件化：PaddleOCR / Vision / Ollama 即插即用。",
            "100% 本地优先：零按量计费、零隐私外泄、响应极快。"
        ], ACCENT_GREEN),
        ("劣势 Weaknesses (内部限制)", [
            "环境依赖重：PySide6/PyTorch/cuDNN 对小白用户安装门槛高。",
            "时间戳精细度不足：尚缺 WhisperX 级别的词级音素对齐。",
            "生态联动不足：尚未直接导出剪辑软件工程文件 (XML/EDL)。",
            "字幕擦除缺失：尚未集成 VSR 等视频画质修复擦除算法。"
        ], ACCENT_AMBER),
        ("机会 Opportunities (外部红利)", [
            "短剧出海爆发：海量成品短剧需逆向提取字幕与 AI 译制。",
            "本地算力升级：个人电脑显卡性能提升，支持更大模型本地化。",
            "开源替代闭源：云端 SaaS 订阅变贵，创作者倾向开源本地工具。",
            "知识库检索刚需：企业视频内容结构化与本地 RAG 需求激增。"
        ], ACCENT_CYAN),
        ("威胁 Threats (外部风险)", [
            "剪映等平台通吃：若官方剪辑工具原生上线硬字幕 OCR 将挤压空间。",
            "新兴竞品追赶：AutoSubs 等工具若引入 OCR 能力将构成正面竞争。",
            "大模型 API 封禁/波动：云端 Vision/纠错 API 成本与稳定性波动。"
        ], ACCENT_ROSE),
    ]

    card_w = Inches(5.72)
    card_h = Inches(2.35)
    coords = [
        (Inches(0.8), Inches(1.9)),
        (Inches(6.8), Inches(1.9)),
        (Inches(0.8), Inches(4.45)),
        (Inches(6.8), Inches(4.45)),
    ]

    for i, (title, points, col) in enumerate(swot_items):
        cx, cy = coords[i]
        add_card(slide16, cx, cy, card_w, card_h, CARD_BG, col)

        tb = slide16.shapes.add_textbox(cx + Inches(0.22), cy + Inches(0.18), card_w - Inches(0.44), card_h - Inches(0.36))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_HEADING
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = col

        for pt_text in points:
            pb = tf.add_paragraph()
            pb.text = f"• {pt_text}"
            pb.font.name = FONT_BODY
            pb.font.size = Pt(9.8)
            pb.font.color.rgb = TEXT_LIGHT
            pb.space_before = Pt(4)

    # ==========================================
    # SLIDE 17: ORCP 核心差异化壁垒与独占价值 (UVP)
    # ==========================================
    slide17 = add_blank_slide()
    add_header(slide17, "Unique Value Proposition", "ORCP 核心差异化壁垒与独占价值 (UVP)", "本地版「通义听悟 + VSE + 智能校对」：重构音视频字幕生产流水线")
    add_footer(slide17, 17)

    uvps = [
        ("01. 双模态视觉+听觉互补提取", "破解单一模态死穴", [
            "纯 ASR 遇到嘈杂背景音、背景音乐重、方言口音时识别率暴跌；纯 OCR 遇到画面遮挡、特效花字时容易漏字。",
            "ORCP 双模态架构让用户可按需选择或互相对齐校验，实现字幕提取准确率极限逼近 99%。"
        ], ACCENT_BLUE),
        ("02. CoT 大模型思维链重构分句", "解决 OCR 碎片化断句顽疾", [
            "传统 OCR 每帧抽样得到的文字是碎片化的，直接生成 SRT 会导致单句过短、断句反人类、时间戳稀碎。",
            "ORCP 独创 CoT 语义感知合并与原文对齐校验，让机器生成的字幕达到人工字幕组分句的自然流畅水准。"
        ], ACCENT_PURPLE),
        ("03. 多引擎即插即用与网关限流", "本地算力与云端大模型无缝融合", [
            "从本地零成本 PaddleOCR，到 Ollama 私有化大模型，再到 OpenAI/DeepSeek 顶级云端 API，自由插拔。",
            "统一网关实现指数退避重试、RPM 滑动窗口限速、响应智能缓存，杜绝 API 限流与重复扣费。"
        ], ACCENT_GREEN),
        ("04. 100% 本地优先与零信任安全", "创作者与企业的隐私安全堡垒", [
            "未上映影视剧、企业核心商业机密、私密录像无需上传云端服务器，规避一切数据合规风险与版权纠纷。",
            "无需支付每月几十美元的 SaaS 订阅或按分钟高昂扣费，一次部署，无限次高并发稳定运行。"
        ], ACCENT_CYAN),
    ]

    card_w = Inches(5.72)
    card_h = Inches(2.35)
    coords = [
        (Inches(0.8), Inches(1.9)),
        (Inches(6.8), Inches(1.9)),
        (Inches(0.8), Inches(4.45)),
        (Inches(6.8), Inches(4.45)),
    ]

    for i, (title, sub, points, col) in enumerate(uvps):
        cx, cy = coords[i]
        add_card(slide17, cx, cy, card_w, card_h, CARD_BG, col)

        tb = slide17.shapes.add_textbox(cx + Inches(0.22), cy + Inches(0.18), card_w - Inches(0.44), card_h - Inches(0.36))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_HEADING
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = col

        p_sub = tf.add_paragraph()
        p_sub.text = f"定位：{sub}"
        p_sub.font.name = FONT_BODY
        p_sub.font.size = Pt(9.5)
        p_sub.font.color.rgb = TEXT_MUTED
        p_sub.space_before = Pt(2)

        for pt_text in points:
            pb = tf.add_paragraph()
            pb.text = f"• {pt_text}"
            pb.font.name = FONT_BODY
            pb.font.size = Pt(9.8)
            pb.font.color.rgb = TEXT_LIGHT
            pb.space_before = Pt(4)

    # ==========================================
    # SLIDE 18: 关键短板与行业基准线对齐
    # ==========================================
    slide18 = add_blank_slide()
    add_header(slide18, "Gap & Benchmarks", "必须对齐的行业硬指标与用户痛点清单", "正视开源社区最高频的吐槽点，将「技术可用」转化为「产品好用」")
    add_footer(slide18, 18)

    gaps = [
        ("痛点 1：安装与依赖极易劝退", "行业痛点：VSE/Whisper 等开源工具最被诟病的就是 CUDA/cuDNN/Torch 环境配置报错。", [
            "对齐方案：提供独立绿色便携版 (Portable Executable) 或嵌入式 uv 自动预编译打包，实现免配置「双击即用」。",
            "收益：用户留存率与社区口碑将呈指数级增长，彻底打破非程序员用户的心理门槛。"
        ], ACCENT_ROSE),
        ("痛点 2：ASR 缺乏词级时间戳与说话人分离", "行业痛点：单纯整句时间戳在多人对话或语速较快时容易产生字幕漂移。", [
            "对齐方案：引入 WhisperX 级别的音素对齐算法与 pyannote 说话人分离 (Diarization)，支持多角色分色显示。",
            "收益：对齐影视翻译与会议纪要顶级工业基准，满足复杂影视剧精细化对齐需求。"
        ], ACCENT_AMBER),
        ("痛点 3：与主流剪辑软件工作流脱节", "行业痛点：仅导出 SRT/TXT 无法直接在剪辑软件中实现样式继承与无缝修改。", [
            "对齐方案：支持一键导出剪映草稿工程 (JianYing Project)、达芬奇 EDL / FCPXML / Premiere 标记点工程。",
            "收益：无缝嵌入剪辑师、二创博主的既有生产流水线，大幅提升工具粘性。"
        ], ACCENT_CYAN),
    ]

    card_w = Inches(3.72)
    card_h = Inches(4.7)
    for i, (title, sub, points, col) in enumerate(gaps):
        cx = Inches(0.8) + i * (card_w + Inches(0.28))
        cy = Inches(1.9)
        add_card(slide18, cx, cy, card_w, card_h, CARD_BG, col)

        tb = slide18.shapes.add_textbox(cx + Inches(0.2), cy + Inches(0.2), card_w - Inches(0.4), card_h - Inches(0.4))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_HEADING
        p.font.size = Pt(12.5)
        p.font.bold = True
        p.font.color.rgb = col

        p_sub = tf.add_paragraph()
        p_sub.text = sub
        p_sub.font.name = FONT_BODY
        p_sub.font.size = Pt(9.5)
        p_sub.font.color.rgb = TEXT_MUTED
        p_sub.space_before = Pt(4)

        for pt_text in points:
            pb = tf.add_paragraph()
            pb.text = f"• {pt_text}"
            pb.font.name = FONT_BODY
            pb.font.size = Pt(9.8)
            pb.font.color.rgb = TEXT_LIGHT
            pb.space_before = Pt(8)

    # ==========================================
    # SLIDE 19: 第五篇章过渡页 / Chapter 5 Divider
    # ==========================================
    slide19 = add_blank_slide()
    add_card(slide19, Inches(0.8), Inches(0.8), Inches(11.733), Inches(5.9), CARD_BG, ACCENT_AMBER)

    tb = slide19.shapes.add_textbox(Inches(1.5), Inches(2.2), Inches(10), Inches(3.0))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "CHAPTER 05"
    p.font.name = FONT_HEADING
    p.font.size = Pt(16)
    p.font.bold = True
    p.font.color.rgb = ACCENT_CYAN

    p2 = tf.add_paragraph()
    p2.text = "战略规划与落地建议"
    p2.font.name = FONT_HEADING
    p2.font.size = Pt(32)
    p2.font.bold = True
    p2.font.color.rgb = TEXT_LIGHT
    p2.space_before = Pt(10)

    p3 = tf.add_paragraph()
    p3.text = "产品演进路线图 (V0.3 -> V0.5 -> V1.0+)、商业化闭环探索与立即启动的执行清单。"
    p3.font.name = FONT_BODY
    p3.font.size = Pt(14)
    p3.font.color.rgb = TEXT_MUTED
    p3.space_before = Pt(15)

    add_footer(slide19, 19)

    # ==========================================
    # SLIDE 20: 产品三阶段演进路线图 (Roadmap)
    # ==========================================
    slide20 = add_blank_slide()
    add_header(slide20, "Product Roadmap", "产品演进路线图：从「单点工具」到「多模态字幕工坊」", "聚焦用户体验极致化，逐步构建「提取 -> 消除 -> 翻译 -> 生产」全链路闭环")
    add_footer(slide20, 20)

    phases = [
        ("阶段一：体验筑基与标准对齐 (V0.3)", "目标：彻底消除安装门槛，对齐工业剪辑标准", [
            "一键免安装便携版：集成 Python 嵌入式运行时与一键依赖检测，告别环境报错。",
            "词级时间戳与说话人分离：引入 WhisperX 算法，支持多人台词自动识别与分色。",
            "剪辑工程导出：支持一键导出剪映草稿 (JianYing Draft)、FCPXML 与达芬奇 EDL。"
        ], ACCENT_BLUE),
        ("阶段二：全链路闭环与场景扩展 (V0.5)", "目标：打通「硬字幕提取 + 视频抹除 + 重填」闭环", [
            "硬字幕擦除集成：融合 VSR/STTN 等 AI 画面修补算法，实现提取后一键去除原画面字幕。",
            "出海短剧场景预设包：预置 20+ 语言提示词模板、俚语库、专有名词表与 CoT 规则。",
            "批量后台自动化处理管线：支持监控文件夹自动轮询转写与多卡并发加速。"
        ], ACCENT_PURPLE),
        ("阶段三：生态化与商业化探索 (V1.0+)", "目标：构建开放插件生态，探索企业私有化部署", [
            "插件扩展市场：开放 OCR/ASR 引擎与第三方 LLM 插件协议，支持社区共建。",
            "企业级私有化定制：面向短剧出海 MCN、广电译制厂提供内网集群部署与 API 网关。",
            "双轨商业模型：核心功能永久开源，高级批量批处理/企业版提供增值技术授权。"
        ], ACCENT_GREEN),
    ]

    card_w = Inches(3.72)
    card_h = Inches(4.7)
    for i, (p_title, p_goal, items, col) in enumerate(phases):
        cx = Inches(0.8) + i * (card_w + Inches(0.28))
        cy = Inches(1.9)
        add_card(slide20, cx, cy, card_w, card_h, CARD_BG, col)

        tb = slide20.shapes.add_textbox(cx + Inches(0.2), cy + Inches(0.2), card_w - Inches(0.4), card_h - Inches(0.4))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = p_title
        p.font.name = FONT_HEADING
        p.font.size = Pt(12)
        p.font.bold = True
        p.font.color.rgb = col

        p_g = tf.add_paragraph()
        p_g.text = p_goal
        p_g.font.name = FONT_BODY
        p_g.font.size = Pt(9.5)
        p_g.font.bold = True
        p_g.font.color.rgb = TEXT_LIGHT
        p_g.space_before = Pt(4)

        for item in items:
            pb = tf.add_paragraph()
            pb.text = f"• {item}"
            pb.font.name = FONT_BODY
            pb.font.size = Pt(9.8)
            pb.font.color.rgb = TEXT_LIGHT
            pb.space_before = Pt(8)

    # ==========================================
    # SLIDE 21: 商业化路径与开源生态运营建议
    # ==========================================
    slide21 = add_blank_slide()
    add_header(slide21, "Monetization & Strategy", "商业化模式探索与开源社区增长飞轮", "以开源核心吸引全球开发者与专业创作者，以增值服务与企业合作实现商业闭环")
    add_footer(slide21, 21)

    strat_cards = [
        ("开源驱动的社区增长飞轮", ACCENT_CYAN, [
            "GitHub 社区裂变：发布详细多语言 README、一键安装 Demo 动图、YouTube/B站教学视频，冲击 5k+ Stars。",
            "创作者口碑传播：针对短剧出海微信群、字幕组论坛、二创社群进行精准定向宣发，以「完全免费+本地保护」击穿口碑。",
            "持续迭代与 Issue 响应：快速修复驱动报错与模型下载问题，建立开发者极高信赖感。"
        ]),
        ("双轨商业化模式 (Dual-Track)", ACCENT_PURPLE, [
            "社区开源版 (Community Edition)：基础多引擎 OCR、faster-whisper ASR、LLM 后处理 100% 免费开源，建立技术标准。",
            "专业增强版 / 插件包 (Pro Add-ons)：集成高阶 VSR 去字幕修复模型、剪映/达芬奇高级联动插件、超分辨率文字增强。",
            "企业私有化部署 (Enterprise License)：为短剧出海机构提供多服务器并发队列、专网私有 LLM 知识库微调与技术支持。"
        ]),
        ("生态跨界合作机会", ACCENT_GREEN, [
            "短剧出海平台合作：与 ReelShort、DramaBox 等海外短剧分发服务商合作，成为官方推荐译制辅助工具。",
            "国产大模型厂商合作：与 DeepSeek、月之暗面、智谱等大模型平台联合推广，作为优秀的本地 Agent/客户端落地案例。"
        ]),
    ]

    card_w = Inches(3.72)
    card_h = Inches(4.7)
    for i, (title, col, items) in enumerate(strat_cards):
        cx = Inches(0.8) + i * (card_w + Inches(0.28))
        cy = Inches(1.9)
        add_card(slide21, cx, cy, card_w, card_h, CARD_BG, col)

        tb = slide21.shapes.add_textbox(cx + Inches(0.2), cy + Inches(0.2), card_w - Inches(0.4), card_h - Inches(0.4))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_HEADING
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = col

        for item in items:
            pb = tf.add_paragraph()
            pb.text = f"• {item}"
            pb.font.name = FONT_BODY
            pb.font.size = Pt(9.8)
            pb.font.color.rgb = TEXT_LIGHT
            pb.space_before = Pt(8)

    # ==========================================
    # SLIDE 22: 核心结论与立即执行清单 (Action Items)
    # ==========================================
    slide22 = add_blank_slide()
    add_header(slide22, "Action Items", "核心研判结论与立即执行清单", "把握短剧出海与 AI 视频风口，以极佳体验和差异化打出破局优势")
    add_footer(slide22, 22)

    actions = [
        ("动作 1：攻克小白用户安装门槛（第一优先级）", [
            "现状：PyTorch/Paddle/CUDA 安装繁琐是开源工具最大劝退点。",
            "行动：基于 PyInstaller / Inno Setup 打包包含嵌入式 Python 的 Windows 独立安装包，一键自动下载默认模型。",
            "目标：将用户首次运行成功率提升至 98% 以上。"
        ], ACCENT_GREEN),
        ("动作 2：打通剪映与达芬奇工程导出（第二优先级）", [
            "现状：仅输出 SRT 文本，用户仍需在剪辑软件中繁琐打轴调整样式。",
            "行动：解析并逆向生成剪映草稿 `draft_content.json`，直接将带样式的字幕轨注入剪映工程。",
            "目标：成为剪映与达芬奇创作者最离不开的「黄金外挂」。"
        ], ACCENT_CYAN),
        ("动作 3：打造短剧出海垂直案例与宣发（第三优先级）", [
            "现状：技术底座强大但缺乏出圈爆款内容案例。",
            "行动：录制「3 分钟搞定一部生肉短剧提取+翻译+压制」的实操短视频，在 GitHub、B站、知乎、微信生态矩阵发布。",
            "目标：首月斩获 1,000+ GitHub Stars，沉淀第一批高活跃核心种子用户。"
        ], ACCENT_AMBER),
    ]

    c_w = Inches(11.733)
    c_h = Inches(1.38)
    for i, (a_title, items, col) in enumerate(actions):
        cy = Inches(1.9) + i * (c_h + Inches(0.2))
        add_card(slide22, Inches(0.8), cy, c_w, c_h, CARD_BG, col)

        tb = slide22.shapes.add_textbox(Inches(1.05), cy + Inches(0.15), Inches(11.2), c_h - Inches(0.3))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = a_title
        p.font.name = FONT_HEADING
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = col

        for item in items:
            pb = tf.add_paragraph()
            pb.text = f"• {item}"
            pb.font.name = FONT_BODY
            pb.font.size = Pt(9.8)
            pb.font.color.rgb = TEXT_LIGHT
            pb.space_before = Pt(3)

    # ==========================================
    # SLIDE 23: 封底 / Thank You Slide
    # ==========================================
    slide23 = add_blank_slide()
    add_card(slide23, Inches(0.8), Inches(0.8), Inches(11.733), Inches(5.9), CARD_BG, ACCENT_BLUE)

    tb = slide23.shapes.add_textbox(Inches(1.5), Inches(2.0), Inches(10.3), Inches(3.5))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

    p = tf.paragraphs[0]
    p.text = "THANK YOU"
    p.font.name = FONT_HEADING
    p.font.size = Pt(38)
    p.font.bold = True
    p.font.color.rgb = ACCENT_CYAN

    p2 = tf.add_paragraph()
    p2.text = "赋能全球音视频创作者 · 打造本地优先的 AI 多模态字幕工坊"
    p2.font.name = FONT_HEADING
    p2.font.size = Pt(20)
    p2.font.bold = True
    p2.font.color.rgb = TEXT_LIGHT
    p2.space_before = Pt(10)

    p3 = tf.add_paragraph()
    p3.text = "数据来源与参考：Grand View Research · QYResearch · 贝哲斯咨询 · 头豹研究院 · GitHub API · 行业实测\n本报告基于 2026 年 8 月最新市场公开数据与竞品实测分析编制。"
    p3.font.name = FONT_BODY
    p3.font.size = Pt(11)
    p3.font.color.rgb = TEXT_MUTED
    p3.space_before = Pt(20)

    # 两个行动链接/提示
    p4 = tf.add_paragraph()
    p4.text = "项目开源主页：https://github.com/yimoyimoyi/orcp  |  欢迎交流合作与产品共建"
    p4.font.name = FONT_BODY
    p4.font.size = Pt(11)
    p4.font.color.rgb = ACCENT_GREEN
    p4.space_before = Pt(15)

    add_footer(slide23, 23)

    output_paths = [
        "ORCP视频字幕提取市场调研报告.pptx",
        "docs/ORCP市场调研报告.pptx",
        "ORCP市场调研报告.pptx"
    ]
    saved = []
    for opath in output_paths:
        try:
            os.makedirs(os.path.dirname(opath) if os.path.dirname(opath) else ".", exist_ok=True)
            prs.save(opath)
            saved.append(opath)
            print(f"Presentation successfully saved to {opath}")
        except Exception as e:
            print(f"Warning: Could not save to {opath}: {e}")

    if not saved:
        print("Error: Could not save to any destination.")


if __name__ == "__main__":
    create_presentation()
