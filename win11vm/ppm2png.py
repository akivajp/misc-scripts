#!/usr/bin/env python3
"""virsh screenshot が出力する PPM (P6) 画像を PNG に変換する（標準ライブラリのみ）。

使い方: ppm2png.py 入力 出力.png
入力がすでに PNG の場合は、そのままコピーする。
"""
from __future__ import annotations

import argparse
import shutil
import struct
import sys
import zlib
from pathlib import Path


def read_ppm(path: Path) -> tuple[int, int, bytes]:
    """PPM (P6、最大値 255) を読み込み、(幅, 高さ, RGB の生データ) を返す。"""
    data = path.read_bytes()
    if not data.startswith(b"P6"):
        raise ValueError(f"P6 形式の PPM ではありません: {path}")
    # ヘッダーの4要素（形式・幅・高さ・最大値）を、コメント行を飛ばしながら読む
    tokens: list[bytes] = []
    pos = 0
    while len(tokens) < 4:
        while data[pos : pos + 1].isspace():
            pos += 1
        if data[pos : pos + 1] == b"#":
            # コメントは行末まで飛ばす
            pos = data.index(b"\n", pos) + 1
            continue
        start = pos
        while not data[pos : pos + 1].isspace():
            pos += 1
        tokens.append(data[start:pos])
    # ヘッダーの直後にある空白1バイトの次から画素データが始まる
    pos += 1
    width, height, maxval = int(tokens[1]), int(tokens[2]), int(tokens[3])
    if maxval != 255:
        raise ValueError(f"最大値が 255 以外の PPM には対応していません: {maxval}")
    pixels = data[pos : pos + width * height * 3]
    if len(pixels) != width * height * 3:
        raise ValueError("画素データが途中で切れています")
    return width, height, pixels


def write_png(path: Path, width: int, height: int, rgb: bytes) -> None:
    """RGB の生データを PNG（8 ビット、トゥルーカラー）として書き出す。"""
    stride = width * 3
    # 各行の先頭に、フィルターの種類 0（なし）を付ける
    raw = b"".join(b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        """長さ・種類・中身・CRC からなる PNG のチャンクを作る。"""
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> int:
    """コマンドラインの入口。"""
    parser = argparse.ArgumentParser(description="PPM (P6) 画像を PNG に変換する（入力が PNG ならそのままコピーする）")
    parser.add_argument("src", type=Path, help="入力ファイル（PPM または PNG）")
    parser.add_argument("dst", type=Path, help="出力する PNG ファイル")
    args = parser.parse_args()
    if args.src.read_bytes()[:8].startswith(b"\x89PNG"):
        shutil.copyfile(args.src, args.dst)
        return 0
    width, height, rgb = read_ppm(args.src)
    write_png(args.dst, width, height, rgb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
