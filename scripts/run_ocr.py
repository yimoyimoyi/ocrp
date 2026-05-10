import os
# 💡 屏蔽联网检查，提升启动速度
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'

import cv2, requests, base64, threading, queue, time, difflib, collections, re
import numpy as np
from paddleocr import PaddleOCR

# ================= 配置 =================
INPUT_DIR = r"C:\Users\32559\Desktop\orcp\input"
OUTPUT_DIR = r"C:\Users\32559\Desktop\orcp\output"
MODEL = "qwen3-vl:4b-instruct"
NUM_AI_THREADS = 2
STRICT_GARBAGE = [
    "內容","內容無","無內容","SKIP","內容：","內容:","波次","UI",
    "人名","人名：","角色：","熟練的指揮","熟练的指挥","剩余回合"
]
GARBAGE_PATTERN = re.compile(r"^(\d+|剩余回合|剩餘回合|回合|[\.\-0-9]+)$")
# ========================================

if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)

pp_ocr = PaddleOCR(use_angle_cls=True, lang="ch", use_gpu=True, show_log=False)
task_queue = queue.Queue(maxsize=100)
video_raw_results = collections.defaultdict(list) # 存储结构改为 (timestamp, content)
print_lock = threading.Lock()
result_lock = threading.Lock()
session = requests.Session()

def get_similarity(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0

def format_time(seconds):
    """将秒数转为 mm:ss 格式"""
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"

def polish_and_save(video_path):
    v_name = os.path.basename(video_path)
    raw_list = video_raw_results[v_name]
    if not raw_list: return

    # raw_list 现在是 [(time_str, content), ...]
    parsed = []
    for t_str, line in raw_list:
        clean_content = line.replace("『", "").replace("』", "").replace("「", "").replace("」", "").strip()
        
        # 基础过滤逻辑
        if not clean_content or clean_content in STRICT_GARBAGE: continue
        if GARBAGE_PATTERN.search(clean_content): continue
        
        speaker = clean_content.split("：", 1)[0] if "：" in clean_content else "NONE"
        parsed.append({"time": t_str, "speaker": speaker, "content": clean_content, "raw": line})

    # 去重逻辑
    refined = []
    for cur in parsed:
        if not refined: 
            refined.append(cur); continue
        last = refined[-1]
        if cur['speaker'] == last['speaker'] and get_similarity(last['content'], cur['content']) > 0.85:
            if len(cur['content']) > len(last['content']):
                refined[-1] = cur
            continue
        refined.append(cur)

    output_file = os.path.join(OUTPUT_DIR, os.path.splitext(v_name)[0] + ".txt")
    seen = set()
    with open(output_file, "w", encoding="utf-8") as f:
        for item in refined:
            final_text = item['raw']
            if "：" in final_text:
                for char in "『』「」[]": final_text = final_text.replace(char, "")
                final_text = final_text.strip()
            
            # 💡 最终保存格式：[时间] 内容
            output_line = f"[{item['time']}] {final_text}"
            if output_line not in seen:
                f.write(output_line + "\n")
                seen.add(output_line)
    
    with print_lock:
        print(f"\n✅ 剧情档案(含时间轴)已生成: {os.path.basename(output_file)}")

# ================= AI 线程 =================
def ai_worker():
    while True:
        task = task_queue.get()
        if task is None: break
        file_path, img_frame, timestamp = task # 💡 接收时间戳
        v_name = os.path.basename(file_path)
        t_str = format_time(timestamp)
        
        h = img_frame.shape[0]
        cropped = img_frame[int(h*0.2):, :]
        _, buffer = cv2.imencode('.jpg', cropped, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        img_b64 = base64.b64encode(buffer).decode('utf-8')

        payload = {
            "model": MODEL,
            "prompt": "你是一个剧本提取专家。请精准识别画面中的文本。对话格式：『人名：内容』。选项格式：『选项：内容』。严禁输出UI标签，其他情况直接输出所有文字，无内容回SKIP。严禁脑补内容",
            "stream": False, "options": {"temperature": 0}, "images": [img_b64]
        }

        try:
            r = session.post("http://localhost:11434/api/generate", json=payload, timeout=20)
            res = r.json().get('response', '').strip()
            if "SKIP" not in res.upper() and len(res) > 1:
                clean_res = res.replace("角色：", "").replace("內容：", "").replace("人名：", "")
                for line in clean_res.split('\n'):
                    content = line.strip()
                    if not content: continue
                    with print_lock:
                        # 💡 实时预览也加上时间，方便你校对
                        print(f"\n\033[K✨ [{t_str}] {content[:50]}", flush=True)
                    with result_lock:
                        video_raw_results[v_name].append((t_str, content))
        except: pass
        finally: task_queue.task_done()

# ================= 视频处理 =================
def process_video(video_path):
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    frame_step = max(1, int(fps * 0.1))
    
    frame_buffer = [] # 动态存放当前这一句的所有帧
    last_sent_text = ""
    last_raw_text = "" # 💡 记录上一帧的文字
    frame_idx = 0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret: break
        
        current_sec = frame_idx / fps

        if frame_idx % frame_step == 0:
            h, w = frame.shape[:2]
            dia_roi = frame[int(h*0.55):int(h*1), :] 
            opt_roi = frame[int(h*0.25):int(h*0.65), :]
            combined = np.vstack([opt_roi, np.zeros((30, w, 3), dtype=np.uint8), dia_roi])
            #roi范围截图调试
            #cv2.imwrite("debug_combined.jpg", combined)


            results = pp_ocr.ocr(combined, cls=True)
            curr_text = "".join([line[1][0] for line in results[0]]).replace(" ", "") if (results and results[0]) else ""

            # 💡 核心：检测字数骤降
            # 如果上一帧文字很长，这一帧突然缩短很多（比如掉过一半或变空），说明上一句结束了
            if len(last_raw_text) > 2 and len(curr_text) < len(last_raw_text) * 0.5:
                if frame_buffer:
                    # 取刚才那一组里最长的一帧送去 AI
                    best_f = max(frame_buffer, key=lambda x: len(x['text']))
                    if get_similarity(best_f['text'], last_sent_text) < 0.8:
                        task_queue.put((video_path, best_f['img'], best_f['ts']), block=True)
                        last_sent_text = best_f['text']
                    frame_buffer = [] # 清空缓存，准备迎接下一句

            # 如果当前有文字，存入 buffer 备选
            if len(curr_text) >= 2:
                frame_buffer.append({"img": frame.copy(), "text": curr_text, "ts": current_sec})
                # 防止 buffer 过大内存溢出，最多保留最近 20 帧采样（约 2 秒）
                if len(frame_buffer) > 8: frame_buffer.pop(0)

            last_raw_text = curr_text # 💡 更新“上一帧”记录

            with print_lock:
                print(f"\r\033[K进度: {int(current_sec)}s | 任务: {task_queue.qsize()} | 哨兵: {curr_text[:12]}", end='', flush=True)
        
        frame_idx += 1

    # 视频结束时，把最后缓存里的抓出来
    if frame_buffer:
        best_f = max(frame_buffer, key=lambda x: len(x['text']))
        if get_similarity(best_f['text'], last_sent_text) < 0.8:
            task_queue.put((video_path, best_f['img'], best_f['ts']), block=True)

    cap.release()
    task_queue.join()
    polish_and_save(video_path)

# ================= 主程序 =================
def main():
    workers = []
    for _ in range(NUM_AI_THREADS):
        t = threading.Thread(target=ai_worker, daemon=True)
        t.start(); workers.append(t)

    video_files = [os.path.join(INPUT_DIR, f) for f in os.listdir(INPUT_DIR) if f.lower().endswith(('.mp4','.mkv'))]
    for v in video_files:
        print(f"\n🎬 正在处理视频: {os.path.basename(v)}")
        process_video(v)

    for _ in range(NUM_AI_THREADS): task_queue.put(None)
    for t in workers: t.join()
    print("\n🏁 剧情提取已完成。")

if __name__ == "__main__":
    main()