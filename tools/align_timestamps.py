#!/usr/bin/env python3
"""
用 faster-whisper 对《走遍美国》每一幕音频做词级识别，再把 episodes.json 里已有的
英文台词逐句对齐回音频时间轴，重算可靠的句级 start/end。

背景：原数据的时间戳与 mp3 实际内容不匹配（ep16-26 音轨长约 2 倍、ep18/19 有整幕
时间戳全为 0、19 幕末句超出音频长度），无法靠加偏移量修正，只能重新对齐。

用法：
    # 单幕试跑
    python tools/align_timestamps.py --act e01_a1 --model small
    # 全量（78 幕）
    python tools/align_timestamps.py --model small
    # 断点续跑（已完成的幕会跳过）
    python tools/align_timestamps.py --model small --resume

输出：
    align_out/<episode_act>.json   每幕一个，含逐句 start/end/conf
    align_out/_report.json         全量统计
"""

import argparse
import difflib
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "align_out"


def norm_token(w: str) -> str:
    """归一化到可比较形式：小写、去标点、去撇号（’ 与 ' 不统一会导致整句匹配失败）。"""
    w = w.replace("\u2019", "'").replace("\u2018", "'").replace("`", "'")
    w = unicodedata.normalize("NFKD", w).lower()
    w = re.sub(r"[^a-z0-9']", "", w)
    return w.replace("'", "")


def tokenize(text: str):
    """英文粗分词，产出 (原始词, 归一化词) 对，保留标点信息用于切句边界。"""
    out = []
    for raw in text.split():
        n = norm_token(raw)
        if n:
            out.append((raw, n))
    return out


def nw_map(orig_seq, hyp_seq, match=1.0, mism=-1.0, gap=-1.5):
    """
    token 级全局对齐（Needleman-Wunsch），返回 {原词位置: 转写词位置}。

    与 difflib 的区别：difflib 递归取「最长匹配块」，遇到本幕这种
    「前情回顾里重播了一遍正片台词」的情况，会把锚点钉在回顾段上，
    导致前后句锚点逆序、中间的句子被压成 0.1 秒的窗口。全局对齐
    带 gap 罚分，选中回顾段会让后面几十句无处安放，代价更高，因此
    自然收敛到正片段；且映射天然严格单调，不可能出现逆序。
    """
    n, m = len(orig_seq), len(hyp_seq)
    prev = [j * gap for j in range(m + 1)]
    ptr = [bytearray(m + 1) for _ in range(n + 1)]
    for j in range(1, m + 1):
        ptr[0][j] = 2
    for i in range(1, n + 1):
        ptr[i][0] = 1
    for i in range(1, n + 1):
        cur = [0.0] * (m + 1)
        cur[0] = i * gap
        ai = orig_seq[i - 1]
        for j in range(1, m + 1):
            best = prev[j - 1] + (match if ai == hyp_seq[j - 1] else mism)
            p = 0
            u = prev[j] + gap
            if u > best:
                best, p = u, 1
            l = cur[j - 1] + gap
            if l > best:
                best, p = l, 2
            cur[j] = best
            ptr[i][j] = p
        prev = cur

    mapping = {}
    i, j = n, m
    while i > 0 or j > 0:
        p = ptr[i][j]
        if i > 0 and j > 0 and p == 0:
            if orig_seq[i - 1] == hyp_seq[j - 1]:
                mapping[i - 1] = j - 1
            i -= 1
            j -= 1
        elif i > 0 and (j == 0 or p == 1):
            i -= 1
        else:
            j -= 1
    return mapping


# 同一句的命中词若横跨这么长的时间空档，说明它一半落在回顾段、一半落在正片段。
# 不切开的话，整句会高亮一条几十秒的色块。
SPLIT_GAP = 2.5


def _split_clusters(hits):
    """把一句的命中词按时间空档聚类，返回词数最多的一簇。"""
    if len(hits) < 2:
        return hits
    clusters = [[hits[0]]]
    for h in hits[1:]:
        if h[0] - clusters[-1][-1][1] > SPLIT_GAP:
            clusters.append([h])
        else:
            clusters[-1].append(h)
    return max(clusters, key=len)


def transcribe_words(model, audio_path, key=None, lang="en"):
    """转写并缓存词流。缓存让调整对齐算法时不必反复跑模型。"""
    cache = OUT_DIR / "_hyp" / f"{key}.json" if key else None
    if cache is not None and cache.exists():
        return [tuple(x) for x in json.loads(cache.read_text())]

    segments, _info = model.transcribe(
        str(audio_path),
        language=lang,
        word_timestamps=True,
        vad_filter=False,
        condition_on_previous_text=False,   # 防止音乐段幻觉级联
        initial_prompt=None,                # 绝不能传台词原文：传了之后 whisper 会把 prompt
                                            # 当成「已转录的前文」直接跳过，开头几句整段失配
        beam_size=5,
        temperature=0.0,
    )
    hyp = []
    for seg in segments:
        for w in (seg.words or []):
            n = norm_token(w.word)
            if n:
                hyp.append((n, float(w.start), float(w.end)))
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(hyp, ensure_ascii=False))
    return hyp


def align_act(model, audio_path: Path, lines, lang="en", method="difflib", key=None):
    """
    返回每句的 {start, end, matched_ratio}。
    做法：把本幕所有台词词扁平化，与 whisper 输出的词流做序列比对，
    块内一一映射，再把映射回填到句子边界。
    method="difflib" 为历史默认；"nw" 用全局对齐，能修重复段抢锚点的问题。
    """
    texts = [l["en"] for l in lines]

    # 扁平化原台词词序列，同时记录每个词属于第几句
    flat = []           # [(token_norm, sentence_idx)]
    for si, t in enumerate(texts):
        for _raw, n in tokenize(t):
            flat.append((n, si))

    if not flat:
        return [{"start": 0.0, "end": 0.0, "matched_ratio": 0.0} for _ in texts]

    hyp = transcribe_words(model, audio_path, key=key, lang=lang)

    if not hyp:
        return [{"start": 0.0, "end": 0.0, "matched_ratio": 0.0} for _ in texts]

    orig_seq = [t for t, _ in flat]
    hyp_seq = [h[0] for h in hyp]

    # 每个原词 -> 命中的 hyp 索引
    if method == "nw":
        orig2hyp = nw_map(orig_seq, hyp_seq)
        orig2hyp = [orig2hyp.get(i) for i in range(len(orig_seq))]
    else:
        orig2hyp = [None] * len(orig_seq)
        sm = difflib.SequenceMatcher(None, orig_seq, hyp_seq, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    orig2hyp[i1 + k] = j1 + k

    # 按句聚合
    sent_hits = {}      # sent_idx -> [(hyp_start, hyp_end, orig_pos)]
    for pos, (tok, si) in enumerate(flat):
        hi = orig2hyp[pos]
        if hi is not None:
            sent_hits.setdefault(si, []).append((hyp[hi][1], hyp[hi][2], pos))
    for si in list(sent_hits):
        sent_hits[si] = _split_clusters(sent_hits[si])

    n_sent = len(texts)
    bound = [None] * n_sent
    for si in range(n_sent):
        hits = sent_hits.get(si)
        if hits:
            bound[si] = {"start": hits[0][0], "end": hits[-1][1], "hits": len(hits)}

    # 未命中的句子：用前后已定位句做线性插值
    known = [i for i in range(n_sent) if bound[i]]
    for si in range(n_sent):
        if bound[si]:
            continue
        prev_i = max([i for i in known if i < si], default=None)
        next_i = min([i for i in known if i > si], default=None)
        if prev_i is not None and next_i is not None:
            iv_start = max(bound[prev_i]["end"], 0.0)
            iv_end = max(bound[next_i]["start"], iv_start + 0.5)
            steps = next_i - prev_i
            step = max((iv_end - iv_start) / steps, 0.15)
            off = si - prev_i
            s = iv_start + step * (off - 1)
            bound[si] = {"start": s, "end": s + step, "hits": 0}
        elif prev_i is not None:
            bound[si] = {"start": bound[prev_i]["end"], "end": bound[prev_i]["end"] + 2.0, "hits": 0}
        elif next_i is not None:
            bound[si] = {"start": max(bound[next_i]["start"] - 2.0, 0.0),
                         "end": bound[next_i]["start"], "hits": 0}
        else:
            bound[si] = {"start": 0.0, "end": 0.0, "hits": 0}

    # 单调化 + 用下一句起点收紧本句终点（句间不留重叠）
    for si in range(n_sent):
        b = bound[si]
        if si > 0 and b["start"] < bound[si - 1]["start"]:
            b["start"] = bound[si - 1]["start"]
        if b["end"] <= b["start"]:
            b["end"] = b["start"] + 1.5
    for si in range(n_sent - 1):
        if bound[si]["end"] > bound[si + 1]["start"]:
            bound[si]["end"] = max(bound[si + 1]["start"], bound[si]["start"] + 0.2)

    # 置信度：该句命中词数 / 该句总词数
    tok_per_sent = [0] * n_sent
    for _tok, si in flat:
        tok_per_sent[si] += 1
    out = []
    for si in range(n_sent):
        b = bound[si]
        ratio = (b["hits"] / tok_per_sent[si]) if tok_per_sent[si] else 0.0
        out.append({
            "start": round(b["start"], 2),
            "end": round(b["end"], 2),
            "matched_ratio": round(ratio, 2),
            "hits": b["hits"],
        })
    return out


def collect_acts(data, only=None):
    acts = []
    for ep in data["episodes"]:
        for ai, a in enumerate(ep["acts"]):
            key = Path(a["audio"]).stem          # e01_a1
            if only and key != only:
                continue
            acts.append((ep["id"], ai, a["label"], key, a["audio"], a["lines"]))
    return acts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="small", help="tiny/base/small/medium/large-v3")
    ap.add_argument("--act", default="", help="只跑某一幕，如 e01_a1")
    ap.add_argument("--resume", action="store_true", help="跳过已存在的输出")
    ap.add_argument("--limit", type=int, default=0, help="最多跑几幕（调试用）")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--method", choices=["difflib", "nw"], default="difflib",
                    help="difflib=历史默认（贪心取最长块）；nw=全局对齐，"
                         "对『回顾段重播正片台词』这类重复内容更稳")
    ap.add_argument("--refresh-hyp", action="store_true",
                    help="忽略转写缓存，重新跑模型")
    args = ap.parse_args()

    if not OUT_DIR.exists():
        OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = json.loads((ROOT / "episodes.json").read_text())
    acts = collect_acts(data, args.act or None)
    if args.limit:
        acts = acts[: args.limit]

    todo = []
    for item in acts:
        fp = OUT_DIR / f"{item[3]}.json"
        if args.resume and fp.exists():
            continue
        todo.append(item)

    print(f"[plan] 共 {len(acts)} 幕，待跑 {len(todo)} 幕，模型={args.model}", flush=True)
    if not todo:
        print("[done] 没有待跑任务")
        return

    if args.refresh_hyp:
        hyp_dir = OUT_DIR / "_hyp"
        if hyp_dir.exists():
            for f in hyp_dir.glob("*.json"):
                if not args.act or f.stem == args.act:
                    f.unlink()

    from faster_whisper import WhisperModel
    t0 = time.time()
    print(f"[load] 加载模型 {args.model} ...", flush=True)
    model = WhisperModel(args.model, device="cpu", compute_type="int8",
                         cpu_threads=args.threads)
    print(f"[load] 完成，耗时 {time.time()-t0:.1f}s", flush=True)

    report = []
    for idx, (eid, ai, label, key, audio_rel, lines) in enumerate(todo, 1):
        audio = ROOT / audio_rel
        ts = time.time()
        try:
            res = align_act(model, audio, lines, method=args.method, key=key)
            avg = sum(r["matched_ratio"] for r in res) / max(len(res), 1)
            payload = {
                "episode": eid, "act_index": ai, "act": label,
                "audio": audio_rel, "model": args.model, "method": args.method,
                "line_count": len(lines), "avg_match": round(avg, 3),
                "lines": res,
            }
            (OUT_DIR / f"{key}.json").write_text(json.dumps(payload, ensure_ascii=False))
            cost = time.time() - ts
            low = sum(1 for r in res if r["matched_ratio"] < 0.5)
            print(f"[{idx}/{len(todo)}] {key} 句数={len(lines)} 平均命中={avg:.2f} "
                  f"低置信句={low} 用时={cost:.0f}s", flush=True)
            report.append({"key": key, "avg_match": round(avg, 3),
                           "low_conf": low, "lines": len(lines), "sec": round(cost)})
        except Exception as e:
            print(f"[{idx}/{len(todo)}] {key} 失败: {e}", flush=True)
            report.append({"key": key, "error": str(e)})

    (OUT_DIR / "_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"[done] 总耗时 {(time.time()-t0)/60:.1f} 分钟", flush=True)


if __name__ == "__main__":
    main()
