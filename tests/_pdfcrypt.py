"""測試用：只用標準函式庫做一頁有文字的 PDF，可選加密（PDF 標準安全處理 R3，RC4 128 位元）。
user=None 不加密；user="" 是只有擁有者密碼（不用密碼就能看，密碼與守門釐清）；user="x" 開檔要密碼。
"""
import hashlib
import struct

PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")
FILE_ID = bytes.fromhex("6b622d746573742d70646663727970741234")[:16]
PERMS = -3904  # 允許列印、複製（一般設定）


def _rc4(key, data):
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    out, i, j = bytearray(), 0, 0
    for b in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(b ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


def _pad(pw):
    return (pw.encode("latin-1") + PAD)[:32]


def _owner_entry(owner, user):
    key = hashlib.md5(_pad(owner or user)).digest()
    for _ in range(50):
        key = hashlib.md5(key).digest()
    data = _rc4(key, _pad(user))
    for i in range(1, 20):
        data = _rc4(bytes(b ^ i for b in key), data)
    return data


def _file_key(user, o):
    h = hashlib.md5(_pad(user) + o + struct.pack("<i", PERMS) + FILE_ID).digest()
    for _ in range(50):
        h = hashlib.md5(h).digest()
    return h


def _user_entry(key):
    data = _rc4(key, hashlib.md5(PAD + FILE_ID).digest())
    for i in range(1, 20):
        data = _rc4(bytes(b ^ i for b in key), data)
    return data + b"\x00" * 16


def _obj_key(key, num, gen=0):
    return hashlib.md5(key + struct.pack("<i", num)[:3] + struct.pack("<i", gen)[:2]).digest()[:16]


def _hex(b):
    return b"<" + b.hex().encode() + b">"


def make_pdf(path, text, user=None, owner="owner-pw"):
    """寫一頁 PDF（內文 text，ASCII）。回 path。"""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    key = None
    o = u = None
    if user is not None:
        o = _owner_entry(owner, user)
        key = _file_key(user, o)
        u = _user_entry(key)
    if key is not None:
        content = _rc4(_obj_key(key, 4), content)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    if key is not None:
        objs.append(b"<< /Filter /Standard /V 2 /R 3 /Length 128 /P " + str(PERMS).encode()
                    + b" /O " + _hex(o) + b" /U " + _hex(u) + b" >>")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    trailer = f"<< /Size {len(objs) + 1} /Root 1 0 R".encode()
    if key is not None:
        trailer += f" /Encrypt {len(objs)} 0 R".encode()
    trailer += b" /ID [" + _hex(FILE_ID) + _hex(FILE_ID) + b"] >>"
    out += b"trailer\n" + trailer + f"\nstartxref\n{xref}\n%%EOF\n".encode()
    with open(path, "wb") as f:
        f.write(bytes(out))
    return path


def encrypted_docx(path):
    """加密後的 OOXML 是 CFB 容器：開頭八個位元組就夠 probe 認（I-014）。"""
    with open(path, "wb") as f:
        f.write(bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 504)
    return path
