"""密碼與守門的測試知識庫（驗收的 <V>）：tests/fixtures/inventory 複本，來源裡另放：
- 客戶案件/信義王/網站密碼.xlsx（內文有 WEB_PW）、客戶案件/信義王/網銀密碼.txt（內文有 BANK_PW）
- 客戶案件/信義王/保險單_王.pdf、客戶案件/大安陳/保險單_陳.pdf（同一組 PDF_PW 加密，放在不同實體的夾）
- 報價與合約/信義王/報價_擁有者.pdf（只有擁有者密碼）、報價與合約/信義王/合約_加密.docx（加密的 Word）
全掃入冊。測試密碼一律是獨特字串，做完 grep 整個知識庫（含 .system 底下）要找不到（開工前補第 2 條）。"""
import os
from pathlib import Path

from tests import _cards
from tests._pdfcrypt import encrypted_docx, make_pdf
from tests.test_lib_inventory_scan import xlsx

WEB_PW = "WebPw-zq7x-5531"
BANK_PW = "BankPw-zq7x-0077"
PDF_PW = "lin2026-zq7x"
GATE_PW = "1234-zq7x"
WEB = "客戶案件/信義王/網站密碼.xlsx"
BANK = "客戶案件/信義王/網銀密碼.txt"
WANG = "客戶案件/信義王/保險單_王.pdf"
CHEN = "客戶案件/大安陳/保險單_陳.pdf"
OWNER_ONLY = "報價與合約/信義王/報價_擁有者.pdf"
ENC_DOCX = "報價與合約/信義王/合約_加密.docx"
SECRETS = (WEB_PW, BANK_PW, PDF_PW, GATE_PW)


def make_pw_vault():
    """回 (tmp, V, raw)。"""
    tmp, V, raw = _cards.make_card_vault()
    xlsx(raw / WEB, (WEB_PW, 1))
    (raw / BANK).write_text(f"網銀帳號 0123\n登入密碼：{BANK_PW}\n", encoding="utf-8")
    make_pdf(raw / WANG, "Insurance policy Wang number 001 annual premium", user=PDF_PW)
    make_pdf(raw / CHEN, "Insurance policy Chen number 002 annual premium", user=PDF_PW)
    make_pdf(raw / OWNER_ONLY, "Quotation owner only protected content here", user="")
    encrypted_docx(raw / ENC_DOCX)
    _cards.scan_apply(V)
    return tmp, V, raw


def leaks(root, secrets=SECRETS):
    """root 底下（含 .system、versions、journal、turns、ledger、hook-errors、temp）哪些檔含任何一個 secrets：回相對路徑清單。
    原檔所在的 raw 不算（呼叫端只給知識庫）。二進位檔照位元組找 UTF-8 編碼。"""
    hits = []
    needles = [s.encode("utf-8") for s in secrets]
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if any(n in data for n in needles):
                hits.append(p.relative_to(root).as_posix())
    return hits
