#!/usr/bin/env python3
"""
计算每幕的 timingSkew，写回 episodes.json，供前端决定是否提示「单句定位可能不准」。

为什么要换指标：原来前端用 alignQuality（平均词命中率）判断，但它衡量的是
「whisper 有没有听出这些词」，不是「时间戳准不准」。短句多的幕（如 e04_a2 满屏
Nope. / Good.）命中率天然偏低，时间戳却是对的，被这个指标冤枉成「对齐差」。

准确的判据是语速的物理下限：人再快也说不到 0.06 秒/词。句长低于这个值，
只能是多句话被压进了同一个时间窗口——这才是真正影响单句定位的错误。

用法：
    python tools/check_timing.py            # 只看报告
    python tools/check_timing.py --write    # 写回 episodes.json
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SEC_PER_WORD = 0.06      # 人语速上限约 0.08 秒/词，低于 0.06 物理上说不完
ALERT_RATIO = 0.05       # 一幕里超过 5% 的句子异常才提示，避免单句误报


def word_count(s: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    path = ROOT / "episodes.json"
    data = json.loads(path.read_text())

    flagged, total = [], 0
    for ep in data["episodes"]:
        for act in ep["acts"]:
            total += 1
            lines = act["lines"]
            n = max(len(lines), 1)
            bad = sum(
                1 for l in lines
                if (l["end"] - l["start"]) < max(word_count(l["en"]), 1) * SEC_PER_WORD
            )
            act["timingSkew"] = round(bad / n, 3)
            act["timingBad"] = bad
            if bad / n >= ALERT_RATIO:
                flagged.append((Path(act["audio"]).stem, bad, len(lines),
                                bad / n, act.get("alignQuality")))

    print(f"已标注 {total} 幕")
    print(f"触发提示 {len(flagged)} 幕（异常句占比 >= {ALERT_RATIO:.0%}）:")
    for key, bad, n, ratio, q in flagged:
        print(f"  {key:9} {bad:2d}/{n:<3d} = {ratio*100:4.1f}%   平均词命中 {q}")

    if args.write:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print("已写入 episodes.json")
    else:
        print("\n(试运行，未写入。加 --write 生效)")


if __name__ == "__main__":
    main()
