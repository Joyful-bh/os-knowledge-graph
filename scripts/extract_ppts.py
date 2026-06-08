"""
批量对 PPT 转 PDF 文件做实体抽取（增量追加模式）。
运行：python scripts/extract_ppts.py
"""

import subprocess
import sys
from pathlib import Path

# PDF 文件 → 章节规范名（与 collaboration_contract.md §6 一致）
TASKS = [
    ("data/raw/1.引论.pdf",           "1.引论"),
    ("data/raw/2.启动.pdf",           "2.启动"),
    ("data/raw/3.0存储管理基础.pdf",   "3.内存管理"),
    ("data/raw/3.1内存管理ld.pdf",     "3.内存管理"),
    ("data/raw/3.2页式内存管理.pdf",   "3.内存管理"),
    ("data/raw/3.3段式内存管理.pdf",   "3.内存管理"),
    ("data/raw/3.4虚拟内存管理.pdf",   "3.内存管理"),
    ("data/raw/3.5自映射.pdf",         "3.内存管理"),
    ("data/raw/4.1进程与线程.pdf",     "4.进程与线程"),
    ("data/raw/4.2进程同步2.pdf",      "4.进程同步"),
    ("data/raw/4.3.调度.pdf",          "4.调度"),
    ("data/raw/4.4.死锁.pdf",          "4.死锁"),
    ("data/raw/5.IO管理.pdf",          "5.IO管理"),
    ("data/raw/6.磁盘管理.pdf",        "6.磁盘管理"),
    ("data/raw/7.文件系统.pdf",        "7.文件系统"),
    ("data/raw/复习.pdf",              "复习"),
]

ROOT = Path(__file__).parent.parent

def main():
    total = len(TASKS)
    for i, (pdf, name) in enumerate(TASKS, 1):
        pdf_path = ROOT / pdf
        if not pdf_path.exists():
            print(f"[{i}/{total}] ⚠️  文件不存在，跳过：{pdf}")
            continue

        print(f"\n[{i}/{total}] {pdf}  →  章节：{name}")
        print("=" * 60)

        result = subprocess.run(
            [sys.executable, "-m", "src.kg.extract",
             "--pdf", str(pdf_path),
             "--name", name],
            cwd=str(ROOT),
        )

        if result.returncode != 0:
            print(f"  ⚠️  抽取失败（exit={result.returncode}），继续下一个文件")

    print("\n" + "=" * 60)
    print("所有 PPT 文件处理完毕。")
    print("下一步：python -m src.kg.normalize --input data/candidates/concepts_raw.json --output data/concepts.json --fast")

if __name__ == "__main__":
    main()
