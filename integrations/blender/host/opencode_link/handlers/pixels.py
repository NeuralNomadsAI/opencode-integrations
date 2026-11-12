# SPDX-License-Identifier: GPL-3.0-or-later

"""PNG encoding, decoding and tiling of RGBA8 pixels, in pure Python (no image datablock, which would
change the document and advance its revision at Blender's next evaluation)."""

import base64
import struct
import zlib

_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode(width, height, rgba, bottom_up=True):
    """PNG bytes of `width` x `height` RGBA8 pixels (rows bottom-up, as OpenGL reads them, by default)."""
    stride = width * 4
    rows = range(height - 1, -1, -1) if bottom_up else range(height)
    raw = b"".join(b"\x00" + rgba[row * stride:(row + 1) * stride] for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return _SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b"")


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def decode(data):
    """(width, height, RGBA8 rows top-down) of an 8-bit, non-interlaced RGB or RGBA PNG."""
    if not data.startswith(_SIGNATURE):
        raise ValueError("not a PNG")
    offset, chunks, header = 8, [], None
    while offset < len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        body = data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            chunks.append(body)
        offset += 12 + length
    width, height, depth, color, _, _, interlace = header
    if depth != 8 or color not in (2, 6) or interlace:
        raise ValueError("only 8-bit non-interlaced RGB(A) PNGs are read")
    channels = 4 if color == 6 else 3
    stride = width * channels
    raw = zlib.decompress(b"".join(chunks))
    previous = bytearray(stride)
    out = bytearray()
    for row in range(height):
        kind = raw[row * (stride + 1)]
        line = bytearray(raw[row * (stride + 1) + 1:(row + 1) * (stride + 1)])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            corner = previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + left) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + up) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif kind == 4:
                line[i] = (line[i] + _paeth(left, up, corner)) & 0xFF
        previous = line
        if channels == 3:
            line = bytearray(b"".join(bytes(line[i:i + 3]) + b"\xff" for i in range(0, stride, 3)))
        out += line
    return width, height, bytes(out)


def tile(images, columns, background=(24, 24, 24, 255), gap=4):
    """One top-down RGBA8 image from same-size top-down tiles laid out in rows of `columns`."""
    width, height = images[0][0], images[0][1]
    rows = (len(images) + columns - 1) // columns
    total_w, total_h = columns * width + (columns + 1) * gap, rows * height + (rows + 1) * gap
    canvas = bytearray(bytes(background) * (total_w * total_h))
    for index, (_, _, pixels) in enumerate(images):
        x0 = gap + (index % columns) * (width + gap)
        y0 = gap + (index // columns) * (height + gap)
        for row in range(height):
            start = ((y0 + row) * total_w + x0) * 4
            canvas[start:start + width * 4] = pixels[row * width * 4:(row + 1) * width * 4]
    return total_w, total_h, bytes(canvas)


def flip(width, height, rgba):
    """Bottom-up rows to top-down."""
    stride = width * 4
    return b"".join(rgba[row * stride:(row + 1) * stride] for row in range(height - 1, -1, -1))


def as_image(png):
    return {"mime": "image/png", "data": base64.b64encode(png).decode("ascii")}
