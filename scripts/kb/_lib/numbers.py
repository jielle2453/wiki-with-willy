"""數字的讀法（從 kb tally 搬來，記帳 1b）：kb tally 與口述加總共用同一支。

收千分位逗號（要合法）、小數、負號、「萬」字尾（乘一萬），只收半形數字；整數最多 15 位、小數最多 4 位。
數字欄不收負數，由呼叫端另擋（parse_number(text, negative=False)）。看不懂丟 NumberRefused（ValueError），訊息就是給人看的那句。
"""
import re
from decimal import Decimal

BAD_NUMBER = "看不懂這個數字：{}"
TOO_BIG = "數字太大：{}"
NEGATIVE = "不能是負數：{}"
_NUMBER = re.compile(r"^(-?)([1-9][0-9]{0,2}(?:,[0-9]{3})+|[0-9]+)(\.[0-9]+)?(萬?)$")  # 只收半形數字（\d 會收全形）
WHOLE_MAX, FRAC_MAX = 15, 4  # 整數、小數最多幾位：遠在 Decimal 預設 28 位精度以內，加總不會失準


class NumberRefused(ValueError):
    """數字看不懂：訊息就是給人看的那句。"""


def parse_number(text, negative=True):
    """「1,280,000」「54萬」「-3.5」→ Decimal；看不懂丟 NumberRefused。negative=False 時負數也拒絕。"""
    text = str(text)
    m = _NUMBER.match(text)
    if not m:
        raise NumberRefused(BAD_NUMBER.format(text))
    sign, whole, frac, wan = m.groups()
    if sign and not negative:
        raise NumberRefused(NEGATIVE.format(text))
    if len(whole.replace(",", "")) > WHOLE_MAX or len((frac or ".")[1:]) > FRAC_MAX:
        raise NumberRefused(TOO_BIG.format(text))
    value = Decimal(sign + whole.replace(",", "") + (frac or ""))
    return value * 10000 if wan else value


def show(value):
    """千分位；整數不帶小數，有小數的去掉尾巴的 0。"""
    value = Decimal(str(value))
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return f"{value:,f}".rstrip("0").rstrip(".")


def plain(value):
    """Decimal → JSON 的數：整數回 int，有小數回 float。"""
    value = Decimal(str(value))
    return int(value) if value == value.to_integral_value() else float(value)
