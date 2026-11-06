#!/usr/bin/env python3
"""
把 align_out/*.json 的对齐结果合并进 episodes.json：
每句新增 start / end（秒，相对本幕音频），保留原 t 作历史参考。

用法：
    python tools/merge_alignment.py            # 试运行，只打印报告
    python tools/merge_alignment.py --write    # 实际写入 episodes.json
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALIGN = ROOT / "align_out"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--min-match", type=float, default=0.3,
                    help="整幕平均命中率低于此值时仍然合并，但会在报告里标红")
    ap.add_argument("--act", action="append", default=[], metavar="KEY",
                    help="只处理指定幕（可重复），如 --act e18_a3。"
                         "不给则处理全部——重跑单幕时务必带上，否则会拿旧的对齐产物覆盖其他幕。")
    args = ap.parse_args()

    data = json.loads((ROOT / "episodes.json").read_text())
    missing, done, skipped = [], 0, 0
    stats = {"lines": 0, "patched": 0, "warn_act": [], "delta": [],
             "filled_zero": 0, "interp": 0}

    for ep in data["episodes"]:
        for act in ep["acts"]:
            key = Path(act["audio"]).stem
            if args.act and key not in args.act:
                continue
            fp = ALIGN / f"{key}.json"
            if not fp.exists():
                missing.append(key)
                continue
            al = json.loads(fp.read_text())
            if len(al["lines"]) != len(act["lines"]):
                print(f"[skip] {key}: 句数不一致 {len(al['lines'])} vs {len(act['lines'])}")
                skipped += 1
                continue
            q = al.get("avg_match", 0)
            act["alignQuality"] = round(q, 2)   # 前端据此提示"本幕时间轴精度较低"
            if q < args.min_match:
                stats["warn_act"].append((key, q))
            for i, line in enumerate(act["lines"]):
                a = al["lines"][i]
                old = line.get("t")
                # 命中 0 的句子也必须用插值值：旧 t 整体偏移 20 秒以上，保留它只会更糟。
                # 插值至少被前后已定位句夹住，误差不会超过一个语段。
                line["start"] = a["start"]
                line["end"] = a["end"]
                stats["patched"] += 1
                if old not in (None, 0, 0.0):
                    stats["delta"].append(a["start"] - old)
                else:
                    stats["filled_zero"] += 1
                if a.get("hits", 0) == 0:
                    stats["interp"] += 1

            # 合并后修一次轻微重叠（对齐阶段插值偶尔会制造 0.1s 级交叠）
            prev_end = 0.0
            for line in act["lines"]:
                if "start" not in line:
                    continue
                if line["start"] < prev_end:
                    line["start"] = round(prev_end, 2)
                    if line["end"] <= line["start"]:
                        line["end"] = round(line["start"] + 0.5, 2)
                prev_end = line["end"]
            stats["lines"] += len(act["lines"])
            done += 1

    d = sorted(stats["delta"])
    print(f"已合并幕数: {done}  跳过: {skipped}  缺对齐文件: {len(missing)}")
    print(f"更新句数: {stats['patched']} / {stats['lines']}"
          f"  ({stats['patched']/max(stats['lines'],1)*100:.1f}%)")
    print(f"  其中修复 t=0 的句子: {stats['filled_zero']}   用插值兜底的: {stats['interp']}")
    if d:
        print("旧时间戳 vs 新起点偏差: 中位 %.1fs  P10 %.1fs  P90 %.1fs  最大 %.1fs"
              % (d[len(d)//2], d[len(d)//10], d[len(d)*9//10], max(abs(d[0]), abs(d[-1]))))
    if stats["warn_act"]:
        print("低质量幕（平均命中 < %.2f）:" % args.min_match)
        for k, m in stats["warn_act"]:
            print(f"   {k}  命中 {m}")
    if missing:
        print("缺对齐文件:", " ".join(missing[:12]) + (" ..." if len(missing) > 12 else ""))

    if args.write:
        bak = ROOT / "episodes.json.bak"
        if not bak.exists():
            shutil.copy2(ROOT / "episodes.json", bak)
            print("已备份原文件 -> episodes.json.bak")
        # 保持 2 空格缩进：压成一行会让每次合并产生上万行假 diff，后续改动无法 review
        (ROOT / "episodes.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print("已写入 episodes.json")
    else:
        print("\n(试运行，未写入。加 --write 生效)")


if __name__ == "__main__":
    main()
