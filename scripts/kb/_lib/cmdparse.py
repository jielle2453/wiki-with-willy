"""指令字串解析：抽路徑、判寫入、判「整句只有一個 kb.py 呼叫」。

字串比對是第二道防線：刻意把路徑拆開的指令擋不住（I-031），所以守門對抽不到路徑的寫入一律擋（unparsed）。
照舊 guard.py 的 outside_quotes、norm_cmd、WRITE_WORDS、REDIRECT、DISCARD_OUTPUT、single_plugin_call、ABS_PATH 改介面。
審閱後補：引號裡的包裝指令（bash -c、cmd /c、powershell -c）整句都看；cd 之後的相對路徑接 cd 的目標；
token 裡夾的絕對路徑（python -c、P=…）抽出來；萬用字元 token 另外回報。
第一輪修正：直譯器只接受裸字或不在知識庫／來源底下的絕對路徑（S1）；引號外的 ( ) 也算串接（S2）；
寫入動詞前一個字元不能是 . 字母 數字 / \\（B1，.md 不是 md）；相對 token 要解析得到才算路徑（B3）。
"""
import base64
import os
import re

from kb._lib.paths import normalize

KB_SCRIPT = "scripts/kb.py"

# 只是把輸出丟掉，不算寫入：2>/dev/null、>/dev/null、2>&1、> nul、> $null、| Out-Null
DISCARD_OUTPUT = re.compile(r"\d?>\s*&\s*(?:\d+|-)(?![\w.\\/-])|\d?>\s*/dev/null|\d?>\s*\$null|\d?>\s*nul\b|\|\s*out-null", re.IGNORECASE)
# 寫入動詞只在 token 開頭：前一個字元不能是 . 字母 數字 _ -（B1：.md、--rm 不算）；
# 前面是 \ 或 / 也算動詞（N-b：\rm、/bin/rm、Microsoft.PowerShell.Management\Remove-Item）
_W = r"(?<![.\w-])"
# 守門補洞 #25（D-348）：PowerShell 別名 clc（Clear-Content）、epcsv（Export-Csv）、sp（Set-ItemProperty）；
# iwr、irm、Invoke-WebRequest、Invoke-RestMethod、PowerShell 的 curl、wget 帶 -OutFile 是寫（目標是 -OutFile 的值），沒帶是讀
WRITE_WORDS = re.compile(
    _W + r"(?:rm|mv|cp|tee|touch|mkdir|rmdir|del|erase|move|copy|ren|rename|truncate|dd|rsync|wget|tar|unzip|install|ln|"
    r"chmod|chown|ri|rd|md|sc|ac|ni|cpi|mi|rni|iex|robocopy|xcopy|zip|7z|compress-archive|expand-archive|shred|unlink|tee-object|"
    r"clc|epcsv|sp)\b|"
    + _W + r"sed(?:\s+-[a-zA-Z]+)*\s+(?:-[a-zA-Z]*i|--in-place)|" + _W + r"perl(?:\s+-[a-zA-Z]+)*\s+-[a-zA-Z]*i|"
    + _W + r"curl\b[^|;&]*\s(?:-[a-zA-Z]*o|-O|--output)\b|-delete\b|"
    + _W + r"(?:iwr|irm|invoke-webrequest|invoke-restmethod|curl|wget)\b[^|;&]*\s-outf\w*|"
    + _W + r"git\s+(?:clean|checkout|restore|reset|stash|rm|mv)\b|"
    + _W + r"(?:remove-item|move-item|copy-item|rename-item|new-item|set-content|add-content|out-file|clear-content|"
    r"set-itemproperty|export-csv|export-clixml|invoke-expression)\b",
    re.IGNORECASE,
)
# 包在殼字串裡的開檔（powershell -Command "Start-Process …"）不拆（D-514 不做），照拿掉之前一樣算寫
WRAPPED_OPEN = re.compile(_W + r"(?:start-process|saps)\b", re.IGNORECASE)
# 守門補洞 #25（D-348）：.NET 的檔案型別（[IO.File]、[System.IO.Directory]、[IO.FileInfo]、([type]'System.IO.File')）。
# 型別名一定要對到：[regex]::Replace、[Array]::Copy、[string]::Copy 不算
_NET_NAME = r"(?:system\.)?io\.(?:file|directory|fileinfo|directoryinfo)"
_NET_TYPE = r"(?:\[\s*" + _NET_NAME + r"\s*\]|\(\s*\[type\]\s*['\"]" + _NET_NAME + r"['\"]\s*\))"
_PAREN_ARGS = r"\((?:[^()'\"]|'[^']*'|\"[^\"]*\")*\)"  # 一層括號，引號裡的括號不算
# 有字面路徑的實例刪除：[IO.FileInfo]::new('<p>').Delete()、(Get-Item '<p>').Delete()（$f.Delete() 沒有字面路徑，不算，決策 6）
_INSTANCE_DELETE = (r"(?:\[\s*(?:system\.)?io\.(?:fileinfo|directoryinfo)\s*\]\s*::\s*new\s*" + _PAREN_ARGS
                    + r"|\(\s*(?:get-item|gi)\s(?:[^()'\"]|'[^']*'|\"[^\"]*\")*\))\s*\.\s*delete\s*\(")
PS_INSTANCE_DELETE = re.compile(_INSTANCE_DELETE, re.IGNORECASE)
# Office 帶路徑的另存（PowerShell 與 python 附著後）：第一個參數是寫入目標。實例刪除與另存不放進 WRITE_CODE（引號裡提到的
# git grep '.SaveAs(' 不算寫）：PowerShell 在 stage_kind 看引號外，直譯器與殼看程式碼
OFFICE_SAVE_METHODS = r"(?:saveas2?|savecopyas|exportasfixedformat)"
_OFFICE_SAVE = r"\." + OFFICE_SAVE_METHODS + r"\s*\("
OFFICE_SAVE = re.compile(_OFFICE_SAVE, re.IGNORECASE)
# 程式碼裡的寫入（python -c、heredoc 餵給直譯器、inline script）：引號內也算。
# 密碼與守門（D-186 (c)）：直譯器的程式碼只看這個，不再看裡面的 > 與寫入動詞，所以這裡要收齊開檔寫、刪、搬、建、開子行程
WRITE_CODE = re.compile(
    r"(open\([^)]*['\"][wax+]|\.open\(\s*['\"][wax+]|\bmode\s*=\s*['\"][wax+]|"
    r"(?:zipfile|tarfile|io|codecs|gzip|bz2|lzma)\.\w*\(?[^)]*['\"][wax]|\bZipFile\([^)]*['\"][wax]|"
    r"\.write_text|\.write_bytes|\.(?:unlink|rmdir|mkdir|touch|rename|replace|symlink_to|hardlink_to|chmod)\(|"
    r"writealltext|writeallbytes|appendalltext|writealllines|appendalllines|"
    + _NET_TYPE + r"\s*::\s*(?:delete|move|copy|create|open|replace|createtext|openwrite|appendtext)\b|"
    r"shutil\.|os\.(?:remove|rename|replace|unlink|makedirs|mkdir|rmdir|removedirs|truncate|link|symlink|chmod|system|popen|startfile)|"
    r"\bunlink\(|subprocess|\bpopen\b|"
    r"writefile|appendfile|unlinksync|renamesync|mkdirsync|rmsync|rmdirsync|copyfile|createwritestream|truncatesync|"
    r"\bfs\.(?:rm|cp|rename|unlink|mkdir|rmdir|copy|symlink|link)\w*\(|child_process|execsync|spawnsync|"
    r"\bopen\s*\(?[^)\n]*['\"]\s*(?:\+?[>|]|>>)|"
    r"file_put_contents|fwrite|\bfopen\([^)]*['\"][wax+]|File\.(?:write|open\([^)]*['\"][wax])|FileUtils)",
    re.IGNORECASE,
)
# open( 的第二個位置參數是寫入模式才算（open('x.md') 的檔名第一個字母不是模式）；第一個參數允許一層括號與引號字串
_OPEN_ARGS = r"\((?:[^,()'\"]|'[^']*'|\"[^\"]*\"|\([^()]*\))*,\s*['\"][rb]*[wax+]"
# 整個字串是開檔模式（裁定 3）：r+b、rb+、w、wt…，後面可接 tarfile 的 :gz、|gz 或 ruby 的 :UTF-8；.open('raw/…')、.Open('a.docx') 不算
_MODE_STR = r"['\"][rb]*[wax+][bt+]*(?:[:|][\w.-]*)?['\"]"
# 放寬甲 U6：直譯器程式碼只看刪檔與覆蓋寫法；搬、改名、建夾、touch、開子行程不算（搬檔改名可以照跑）。
# 跑別的指令的（subprocess、os.system…）另外抽字串常數照指令判（guard._code_commands），不是常數的照 U5
# 守門補洞 #25：mode= 與 .open( 收 r+b、rb+（[rb]* 開頭），整個字串都是模式才算（_MODE_STR，可帶 :gz、:UTF-8 這類後綴，裁定 3：
# .open('raw/…')、.Open('a.docx') 的路徑不是模式）；os.open 帶 O_WRONLY／O_RDWR／O_TRUNC。
# WRITE_CODE 的開檔模式照原本的 [wax+] 開頭（只決定這段算不算寫；改成整串會讓 git grep "mode='rb+'" 這類讀變成寫）
DESTRUCTIVE_CODE = re.compile(
    r"(open" + _OPEN_ARGS + r"|\.open\(\s*" + _MODE_STR + r"|\bmode\s*=\s*" + _MODE_STR + r"|"
    r"os\.open\((?:[^()'\"]|'[^']*'|\"[^\"]*\"|\([^()]*\))*?\bO_(?:WRONLY|RDWR|TRUNC)\b|"
    r"(?:zipfile|tarfile|io|codecs|gzip|bz2|lzma)\.\w*\(?[^)]*['\"][wax]|\bZipFile\([^)]*['\"][wax]|"
    r"\.write_text|\.write_bytes|\.(?:unlink|rmdir|replace)\(|"
    r"writealltext|writeallbytes|appendalltext|writealllines|appendalllines|" + _NET_TYPE + r"\s*::\s*delete\b|"
    r"shutil\.(?:rmtree|copy\w*)|os\.(?:remove|replace|unlink|rmdir|removedirs|truncate)|"
    r"\bunlink\(|\bremove_tree\b|\brmtree\b|"
    r"writefile|appendfile|unlinksync|rmsync|rmdirsync|copyfile|createwritestream|truncatesync|"
    r"\bfs\.(?:rm|cp|unlink|rmdir|copy)\w*\(|"
    r"\bopen\s*\(?[^)\n]*['\"]\s*(?:\+?[>|]|>>)|"
    r"file_put_contents|fwrite|\bfopen" + _OPEN_ARGS + r"|File\.(?:write|delete|unlink|open" + _OPEN_ARGS + r")|"
    r"FileUtils\.(?:rm|remove|cp|copy|install))",
    re.IGNORECASE,
)
# 腳本檔副檔名：直譯器、殼的第一個存在的腳本檔參數，內容當程式碼判（放寬甲 U6）
SCRIPT_EXTS = (".py", ".js", ".mjs", ".ts", ".rb", ".pl", ".php", ".ps1", ".bat", ".cmd", ".sh", ".vbs")
SHELL_SCRIPT_EXTS = (".ps1", ".bat", ".cmd", ".sh")  # 內容是指令，照整套判斷（遞迴）
# 會把引號裡的字串當指令跑的包裝：出現時整句（含引號內）都看
WRAPPER = re.compile(
    r"(?:^|[\s;&|(])(?:bash|sh|zsh|dash|ksh|cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh(?:\.exe)?|eval|exec|source|"
    r"python3?|py|node|perl|ruby|php)\b",
    re.IGNORECASE,
)
# 密碼與守門（D-186 (c)）：包裝分兩種。殼（引號裡是指令）照舊整段看 > 與寫入動詞；直譯器（引號裡、heredoc 裡是程式碼）只看 WRITE_CODE
SHELL_WRAPPERS = {"bash", "sh", "zsh", "dash", "ksh", "cmd", "powershell", "pwsh", "eval", "exec", "source", ".",
                  "iex", "invoke-expression", "wsl", "busybox",
                  "watch", "parallel"}  # watch、parallel 把後面的字串交給殼跑（測試 28 N1）；start-process、saps、start 走開檔段（D-514）
# 包一層再跑後面指令的（測試 28 N1，跟 env、timeout、nohup 一樣）：後面接殼或直譯器時，這一段照那個殼或直譯器判。
# -exec 這幾個是 find 的動作
EXEC_WRAPPERS = {"xargs", "setsid", "stdbuf", "env", "timeout", "nohup", "nice", "ionice", "sudo", "doas", "time", "command",
                 "builtin", "exec", "chrt", "taskset", "flock", "unbuffer", "-exec", "-execdir", "-ok", "-okdir"}
INTERPRETERS = {"python", "python3", "py", "node", "perl", "ruby", "php", "pythonw", "deno", "bun", "osascript", "cscript",
                "wscript", "java", "dotnet", "r", "rscript", "lua", "tclsh", "julia"}
# 已知只讀的指令（D-186 補、D-185）：這一段只有導向才是寫；參數裡出現寫入動詞（grep md、echo rm）是資料
READONLY_VERBS = {"cat", "type", "more", "less", "head", "tail", "grep", "egrep", "fgrep", "rg", "findstr", "select-string",
                  "sls", "wc", "sort", "uniq", "cut", "diff", "cmp", "fc", "file", "stat", "ls", "dir", "gci", "get-childitem",
                  "tree", "find", "get-content", "gc", "test-path", "get-item", "gi", "get-itemproperty", "gp", "resolve-path",
                  "rvpa", "split-path", "join-path", "measure-object", "select-object", "where-object", "format-table",
                  "format-list", "out-string", "out-host", "echo", "printf", "write-output", "write-host", "pwd", "get-location",
                  "gl", "test", "[", "realpath", "readlink", "basename", "dirname", "md5sum", "sha1sum", "sha256sum",
                  "get-filehash", "od", "xxd", "hexdump", "strings", "jq", "true", "false", "nl", "column", "tac", "rev",
                  "where", "which", "get-command", "date", "get-date", "whoami", "hostname", "convertfrom-json",
                  "convertto-json", "sleep", "start-sleep"}
# 只讀動詞的輸出檔（測試 28 F2-5）：(短旗標裡的字母, 長旗標)；帶了就是寫，這段的路徑都是目標
OUTPUT_FLAGS = {"sort": ("o", ("--output",)), "less": ("oO", ("--log-file",)), "tree": ("o", ("--output",))}
# 第二個位置參數是輸出檔的只讀動詞：值放在下一個字的旗標
POSITIONAL_OUTPUT = {"uniq": {"-f", "-s", "-w"},
                     "xxd": {"-c", "-g", "-l", "-s", "-o", "-n", "-cols", "-groupsize", "-len", "-seek", "-name", "-offset"}}
# perl、ruby、php 的內建刪改檔與跑指令（測試 28 F2-4）：只在動詞是它們時看，git commit -m "unlink…" 不算
SCRIPT_BUILTIN_VERBS = {"perl", "ruby", "php"}
SCRIPT_BUILTIN_WRITE = re.compile(
    r"\b(?:unlink|rename|mkdir|rmdir|symlink|truncate|chmod|chown|utime|sysopen|system|exec|qx|syscall|spawn|"
    r"make_path|mkpath|remove_tree|rmtree|shell_exec|passthru|proc_open|popen)\b|\b(?:copy|move)\s*\(|File::\w+|"
    r"\bFile\.(?:delete|unlink|rename|symlink|link|truncate|chmod|write|binwrite)|\bDir\.(?:mkdir|rmdir|delete|unlink)|"
    r"\bIO\.(?:write|binwrite|popen)|%x|`",
    re.IGNORECASE,
)
# find 帶這些就不是只讀（會跑別的指令或刪檔）
FIND_ACTIONS = re.compile(r"(?:^|\s)-(?:exec|execdir|ok|okdir|delete|fprint\w*|fls)\b", re.IGNORECASE)
# 列檔名（D-185）：find 只准帶這些
FIND_LIST_FLAGS = {"-name", "-iname", "-type", "-maxdepth", "-mindepth", "-print"}
LIST_ONLY_VERBS = {"ls", "dir", "tree", "find", "gci", "get-childitem"}
# 寫入那段的參數從管線前面來（xargs、ForEach-Object；PowerShell 的 cmdlet 會把前面的物件綁成 -Path）：管線前面的路徑都算目標
PIPE_ARG_VERBS = {"xargs", "parallel", "foreach-object", "%", "foreach"}
# 管線後面寫的是前一段的內容、目標是自己點名的檔（Get-Content a | Set-Content b）：不是拿前一段的輸出當路徑
CONTENT_SINKS = {"set-content", "sc", "add-content", "ac", "out-file", "tee-object", "tee", "export-csv", "export-clixml",
                 "epcsv"}
CD_ONLY_VERBS = {"cd", "pushd", "popd", "set-location", "sl", "chdir", "push-location", "pop-location", "pwd", "get-location", "gl"}
# 指令前綴：後面那個字才是動詞
VERB_PREFIXES = {"sudo", "time", "nohup", "env", "nice", "command", "builtin", "exec", "timeout", "stdbuf", "ionice", "&", "!",
                 "then", "do", "else", "if", "while", "until", "{", "("}
DATA_MARK = "__KBDATA{}__"
_DATA_MARK = re.compile(r"__KBDATA(\d+)__")
_HEREDOC_OP = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?([A-Za-z_][\w.-]*))")


def split_bodies(command, powershell=False):
    """D-186 (a)：heredoc（<<WORD、<<'WORD'、<<"WORD"、<<-WORD）與 PowerShell here-string（@' … '@、@" … "@）的內文拿出來當資料。
    回 (clean, bodies)：clean 裡每段內文換成一個記號 __KBDATAn__（heredoc 連 << 運算子一起換），bodies[n] 是內文。
    引號裡的 << 不算；內文沒有結束行就收到最後。"""
    if powershell:
        return _split_herestrings(command)
    out, bodies, pending = [], Bodies(), []
    i, n, quote = 0, len(command), None
    while i < n:
        ch = command[i]
        if quote:
            out.append(ch)
            if ch == "\\" and quote in ('"', "$'") and i + 1 < n:
                out.append(command[i + 1])
                i += 2
                continue
            if ch == quote[-1]:
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            out.append(command[i:i + 2])
            i += 2
            continue
        if command.startswith("$'", i):
            quote = "$'"  # $'…' 裡 \' 是跳脫（不然 $'\'' # … 會被當成註解拿掉）
            out.append("$'")
            i += 2
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "#" and (i == 0 or command[i - 1] in " \t\n;|&("):
            j = command.find("\n", i)
            i = n if j < 0 else j  # 註解不跑：整段拿掉（註解裡的 << 不是 heredoc，裡面的引號也不能吃掉下一行）
            continue
        if command.startswith("<<<", i):
            # here-string（測試 28 N1）：跟 heredoc 一樣是餵進去的內文，給殼或直譯器就是程式碼；只有整個單引號包住的不展開 $(…)
            k = i + 3
            while k < n and command[k] in " \t":
                k += 1
            word, end = _shell_word(command, k)
            raw = command[k:end]
            bodies.append(word)
            bodies.expands.append(not (len(raw) >= 2 and raw[0] == raw[-1] == "'" and raw.count("'") == 2))
            out.append(" " + DATA_MARK.format(len(bodies) - 1) + " ")
            i = end
            continue
        if command.startswith("<<", i):
            k = i + 2
            strip_tabs = k < n and command[k] == "-"
            k += 1 if strip_tabs else 0
            while k < n and command[k] in " \t":
                k += 1
            word, end = _shell_word(command, k)
            if word:
                pending.append((word, strip_tabs, len(bodies)))
                bodies.append("")
                # <<EOF 的內文會展開 $(…) 與反引號；結束字有引號或反斜線（<<'EOF'、<<"EOF"、<<\EOF）才是純資料
                bodies.expands.append(not any(c in command[k:end] for c in "'\"\\"))
                out.append(" " + DATA_MARK.format(len(bodies) - 1) + " ")
                i = end
                continue
        if ch == "\n" and pending:
            out.append("\n")
            i += 1
            for word, strip_tabs, idx in pending:
                lines = []
                while i < n:
                    j = command.find("\n", i)
                    line = command[i:] if j < 0 else command[i:j]
                    i = n if j < 0 else j + 1
                    if (line.lstrip("\t") if strip_tabs else line).rstrip("\r") == word:
                        break
                    lines.append(line)
                bodies[idx] = "\n".join(lines)
            pending = []
            continue
        out.append(ch)
        i += 1
    return "".join(out), bodies


def _shell_word(command, k):
    """heredoc 的結束字照 bash 的規則：一個 shell 字（到空白或 ; | & < > ( ) 為止），引號與反斜線拿掉再比（<<'E'"O"F 是 EOF）。
    回 (字, 字結束的位置)；不是字回 ("", k)。"""
    out, quote, n = [], None, len(command)
    while k < n:
        ch = command[k]
        if quote:
            if ch == quote:
                quote = None
            else:
                out.append(ch)
        elif ch in "\"'":
            quote = ch
        elif ch == "\\" and k + 1 < n:
            k += 1
            out.append(command[k])
        elif ch.isspace() or ch in ";|&<>()":
            break
        else:
            out.append(ch)
        k += 1
    return "".join(out), k


def _split_herestrings(command):
    """PowerShell：@' … '@、@" … "@ 的內文換成記號。照引號與註解走（字串裡、# 註解裡的 @' 不是 here-string），
    結束行是開頭就是 '@ 或 "@ 的那一行；沒有結束行就收到最後。"""
    out, bodies, quote, i, n = [], Bodies(), None, 0, len(command)
    while i < n:
        ch = command[i]
        if quote:
            out.append(ch)
            if ch == "`" and quote == '"' and i + 1 < n:
                out.append(command[i + 1])
                i += 2
                continue
            if ch == quote:
                if quote == "'" and command.startswith("''", i):
                    out.append("'")
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch == "`" and i + 1 < n:
            out.append(command[i:i + 2])  # 引號外的反引號跳脫下一個字（`' 不是引號）
            i += 2
            continue
        if ch == "#" and (i == 0 or command[i - 1] in " \t\n;|&({"):
            j = command.find("\n", i)
            i = n if j < 0 else j  # 註解不跑：整段拿掉（同 bash）
            continue
        m = re.match(r"@(['\"])[ \t]*\r?\n", command[i:])
        if m:
            close = "\n" + m.group(1) + "@"
            start = i + m.end()
            j = command.find(close, start - 1)
            body = command[start:j if j >= 0 else n].rstrip("\r")
            bodies.append(body)
            bodies.expands.append(m.group(1) == '"')  # @" "@ 會展開 $(…)，@' '@ 是純資料
            out.append(" " + DATA_MARK.format(len(bodies) - 1) + " ")
            i = (j + len(close)) if j >= 0 else n
            continue
        if ch in "\"'":
            quote = ch
        out.append(ch)
        i += 1
    return "".join(out), bodies


class Bodies(list):
    """split_bodies 的內文清單；expands[n] 是第 n 段會不會展開 $(…)（bash 沒加引號的 heredoc、PowerShell 的 @" "@）。"""

    def __init__(self):
        super().__init__()
        self.expands = []


class Unbalanced(ValueError):
    """指令替換的括號或反引號沒收尾：拆不出來，呼叫端照 unparsed 擋。"""


def _group(text, i, close, powershell):
    """text[i] 是開括號之後的第一個字：找對應的 close（照引號與巢狀），回 (內文, 收尾之後的位置)；沒收尾丟 Unbalanced。"""
    opener = {")": "(", "}": "{"}[close]
    depth, quote, j, n = 1, None, i, len(text)
    esc = "`" if powershell else "\\"
    while j < n:
        ch = text[j]
        if quote:
            if ch == esc and quote in ('"', "$'") and j + 1 < n:
                j += 2
                continue
            if ch == quote[-1]:
                quote = None
        elif ch == esc and j + 1 < n:
            j += 2
            continue
        elif not powershell and text.startswith("$'", j):
            quote = "$'"
            j += 2
            continue
        elif ch in "\"'":
            quote = ch
        elif ch == opener:
            depth += 1
        elif ch == close:
            depth -= 1
            if depth == 0:
                return text[i:j], j + 1
        j += 1
    raise Unbalanced(text[i:i + 40])


def substitutions(text, powershell=False, literal=False):
    """會被執行的內層指令（測試 28，D-186）：回 [(位置, 內文)]，位置是在 text 裡開始的地方（給呼叫端算那時的效用 cwd）。
    Bash：$(…)、反引號、<(…)、>(…)；雙引號裡的 $(…) 與反引號照樣執行，單引號裡不算。
    PowerShell：$(…)、@(…)、(…) 子運算式、{…} 指令區塊（${名稱} 是變數，不算）；雙引號裡只有 $(…)，單引號裡不算。
    literal：heredoc／here-string 的內文，引號只是字（只找 $(…)，bash 另找反引號）。括號或反引號沒收尾丟 Unbalanced。"""
    out, quote, i, n = [], None, 0, len(text)
    esc = "`" if powershell else "\\"
    while i < n:
        ch = text[i]
        if not literal and quote == "'":
            if ch == "'":
                if powershell and text.startswith("''", i):
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if quote == "$'":  # bash 的 $'…'：\ 跳脫、不執行任何東西
            i += 2 if ch == "\\" else 1
            if ch == "'":
                quote = None
            continue
        if not powershell and not literal and not quote and text.startswith("$'", i):
            quote = "$'"
            i += 2
            continue
        if ch == esc and i + 1 < n:
            i += 2
            continue
        if text.startswith("$(", i) or (not quote and not literal and (
                (powershell and text.startswith("@(", i)) or (not powershell and text.startswith(("<(", ">("), i)))):
            inner, end = _group(text, i + 2, ")", powershell)
            out.append((i, inner))
            out.extend((i + 2 + p, s) for p, s in substitutions(inner, powershell))
            i = end
            continue
        if ch == "`" and not powershell:
            j = i + 1
            while j < n and not (text[j] == "`" and text[j - 1] != "\\"):
                j += 1
            if j >= n:
                raise Unbalanced(text[i:i + 40])
            out.append((i, text[i + 1:j]))
            i = j + 1
            continue
        if powershell and not quote and not literal and ch in "({" and not (ch == "{" and i > 0 and text[i - 1] == "$"):
            inner, end = _group(text, i + 1, ")" if ch == "(" else "}", powershell)
            out.append((i, inner))
            out.extend((i + 1 + p, s) for p, s in substitutions(inner, powershell))
            i = end
            continue
        if not literal:
            if ch == '"' and quote == '"':
                quote = None
            elif ch in "\"'" and not quote:
                quote = ch
        i += 1
    return out


def data_marks(text):
    """一段指令裡的內文記號編號。"""
    return [int(x) for x in _DATA_MARK.findall(text)]


def strip_marks(text):
    return _DATA_MARK.sub(" ", text)


def verb_of(words):
    """一段的動詞：跳過前綴（sudo、time、env、timeout N…）與變數設定（X=1、$x =），回小寫、去路徑與 .exe；沒有回空字串。"""
    i = 0
    while i < len(words):
        w = words[i]
        low = w.lower()
        if not w or low in VERB_PREFIXES or re.match(r"^\w+=", w):
            i += 1
            if low == "timeout" and i < len(words) and re.match(r"^\d", words[i]):
                i += 1
            continue
        if w.startswith("$") and i + 1 < len(words) and words[i + 1] == "=":
            i += 2
            continue
        if w.startswith("$") and w.endswith("="):
            i += 1
            continue
        base = low.replace("\\", "/").rsplit("/", 1)[-1]
        return base[:-4] if base.endswith(".exe") else base
    return ""


def _base(word):
    """一個字當動詞看：小寫、去路徑與 .exe。"""
    base = word.lower().replace("\\", "/").rsplit("/", 1)[-1]
    return base[:-4] if base.endswith(".exe") else base


def inner_verb(verb, words):
    """包一層再跑的（xargs sh -c、find -exec bash -c、setsid、stdbuf -o0 sh -c…）：包的那層後面接殼或直譯器，回那個殼或直譯器；
    不然回 verb（測試 28 N1）。"""
    if verb in SHELL_WRAPPERS or verb in INTERPRETERS:
        return verb
    wrapped = False
    for w in words:
        b = _base(w)
        if wrapped and (b in INTERPRETERS or (b in SHELL_WRAPPERS and b not in (".", "exec"))):
            return b
        if b in EXEC_WRAPPERS:
            wrapped = True
    return verb


def readonly_writes(verb, args):
    """只讀動詞也會寫檔的寫法（測試 28 F2-5）：sort -o、less -o／-O／--log-file、tree -o 的輸出檔；uniq、xxd 的第二個位置參數是輸出檔。
    args 是這段的字（不含導向目標），從動詞之後看。"""
    i = next((k for k, w in enumerate(args) if verb_of([w]) == verb), None)
    if i is None:
        return False
    rest = args[i + 1:]
    if verb in OUTPUT_FLAGS:
        letters, longs = OUTPUT_FLAGS[verb]
        for w in rest:
            low = w.lower()
            if low.startswith("--"):
                if any(low == x or low.startswith(x + "=") for x in longs):
                    return True
            elif w.startswith("-") and any(c in letters for c in w[1:]):
                return True
        return False
    if verb in POSITIONAL_OUTPUT:
        takes_value, count, k = POSITIONAL_OUTPUT[verb], 0, 0
        while k < len(rest):
            w = rest[k]
            if w == "--":
                count += len(rest) - k - 1
                break
            if w.startswith("-") and w != "-":
                k += 2 if w.lower() in takes_value else 1
                continue
            count += 1
            k += 1
        return count >= 2
    return False


def stage_kind(verb, text, code="", args=None, powershell=False):
    """D-186：一段指令的種類。text 是這段（記號已換回空白）、code 是要當程式碼看的內文（這段的 heredoc、或管線前面餵過來的）。
    args：這段的字（不含導向目標），只讀動詞看輸出旗標與位置參數用。
    回 "write"（寫：參數都是寫的目標）、"read"（只讀或不知道會不會寫的執行檔）。導向另外算。
    開檔段（D-514）：只有 -RedirectStandardOutput／-RedirectStandardError 是寫；開的是程式時由 guard 把那行當內層指令判。"""
    opened = open_stage(verb, raw_words(text, powershell), powershell)
    if opened is not None:
        return "write" if opened["writes"] else "read"
    bare = outside_quotes(text, powershell)
    unquoted = bare if bare is not None else text
    if powershell and (PS_INSTANCE_DELETE.search(unquoted) or OFFICE_SAVE.search(unquoted)):
        return "write"  # (Get-Item '<p>').Delete()、$d.SaveAs2('<p>')：看引號外；Get-Item 是只讀動詞，整段是刪（守門補洞 #25）
    if verb in READONLY_VERBS and not (verb == "find" and FIND_ACTIONS.search(unquoted)):
        return "write" if readonly_writes(verb, args or []) else "read"
    if verb in SHELL_WRAPPERS:
        scope = text + "\n" + code
        return "write" if (REDIRECT.search(scope) or WRITE_WORDS.search(scope) or WRITE_CODE.search(scope)
                           or WRAPPED_OPEN.search(scope) or OFFICE_SAVE.search(scope) or PS_INSTANCE_DELETE.search(scope)) \
            else "read"
    if verb in INTERPRETERS:
        if verb in SCRIPT_BUILTIN_VERBS and (SCRIPT_BUILTIN_WRITE.search(text) or SCRIPT_BUILTIN_WRITE.search(code)):
            return "write"  # perl -e 'unlink q(…)'（F2-4）：perl、ruby、php 的內建刪改檔與跑指令
        return "write" if (WRITE_CODE.search(text) or WRITE_CODE.search(code) or WRITE_WORDS.search(unquoted)
                           or OFFICE_SAVE.search(text) or OFFICE_SAVE.search(code)) else "read"
    if WRITE_WORDS.search(unquoted) or WRITE_CODE.search(text) or WRITE_CODE.search(code):
        return "write"
    return "read"


# 守門補洞 #25（D-348）：-EncodedCommand（-e、-ec、-en… 到 -encodedcommand，-、-- 或 / 開頭，值可用冒號接）解開當 -Command 的內容判
_PS_HOSTS = ("powershell", "pwsh")


def encoded_command(words):
    """powershell／pwsh 的 -EncodedCommand：回解開的指令；沒帶這個旗標回 None；值沒給或解不開（不是 base64、不是 UTF-16LE）
    丟 Unbalanced（呼叫端照 unparsed 擋）。-Command、-File（含縮寫 -co、-fi…）與 .ps1 腳本之後的字是指令或腳本的參數，不再找。"""
    i = next((k for k, w in enumerate(words) if _base(w) in _PS_HOSTS), None)
    if i is None:
        return None
    rest = words[i + 1:]
    for k, w in enumerate(rest):
        if w.lower().endswith(".ps1"):
            return None  # pwsh ./x.ps1 -e prod、pwsh /c/x.ps1 -e prod：後面是腳本的參數
        if len(w) < 2 or w[0] not in "-/":
            continue
        name, colon, value = w[2 if w.startswith("--") else 1:].partition(":")
        name = name.lower()
        if name and not colon and ("command".startswith(name) or "file".startswith(name)):
            return None
        if name in ("e", "ec") or (len(name) >= 2 and "encodedcommand".startswith(name)):
            if not value:  # -EncodedCommand <b64>、-e: <b64>；-EncodedCommand:<b64> 冒號寫法的值在同一個字（裁定 5）
                if k + 1 >= len(rest):
                    raise Unbalanced(w)
                value = rest[k + 1]
            try:
                return base64.b64decode(value, validate=True).decode("utf-16-le")
            except (ValueError, UnicodeDecodeError) as exc:  # binascii.Error 是 ValueError
                raise Unbalanced(value[:40]) from exc
    return None


# .NET 檔案靜態呼叫的方法對到動作；Open 只有寫入或截斷模式才算寫
_NET_CALL = re.compile(_NET_TYPE + r"\s*::\s*(\w+)\s*\(", re.IGNORECASE)
_NET_METHODS = {"delete": "delete", "writealltext": "write", "writeallbytes": "write", "writealllines": "write",
                "appendalltext": "write", "appendalllines": "write", "create": "write", "replace": "replace", "open": "open",
                "move": "move", "copy": "copy", "createtext": "write", "openwrite": "write", "appendtext": "write"}
_OPEN_WRITE_MODE = re.compile(r"truncate|append|create|write", re.IGNORECASE)
_OPEN_WRITE_NUM = ({"2", "4", "5", "6"}, {"2", "3"})  # 寫成數字時：第二個參數 FileMode（Create、OpenOrCreate、Truncate、Append）、第三個 FileAccess（Write、ReadWrite）
_NET_NEW = re.compile(r"\[\s*(?:system\.)?io\.(?:fileinfo|directoryinfo)\s*\]\s*::\s*new\s*\(", re.IGNORECASE)
_GET_ITEM_GROUP = re.compile(r"\(\s*(?:get-item|gi)\s", re.IGNORECASE)
_DELETE_AFTER = re.compile(r"\s*\.\s*delete\s*\(", re.IGNORECASE)
# 第一個參數是字面路徑；Excel 的 ExportAsFixedFormat(<型別>, <路徑>) 路徑在第二個；python 的 FileName=、OutputFileName= 具名參數（裁定 5）
_OFFICE_TARGET = re.compile(
    r"\.(?:(?:saveas2?|savecopyas|exportasfixedformat)\s*\(\s*(?:\[ref\]\s*)?"
    r"|exportasfixedformat\s*\(\s*[^,()'\"]+,\s*(?:\[ref\]\s*)?"
    r"|(?:saveas2?|savecopyas|exportasfixedformat)\s*\([^()]*?\b(?:output)?filename\s*=\s*)"
    r"[rRuU]?(?:'([^'\n]+)'|\"([^\"\n]+)\")", re.IGNORECASE)


def _split_args(inner):
    """括號裡的參數照頂層逗號拆（引號與括號裡的逗號不算），去頭尾空白。"""
    parts, buf, quote, depth = [], [], None, 0
    for ch in inner:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf).strip())
    return [p for p in parts if p]


def _call_args(text, start):
    """text[start] 是開括號之後：回 (參數清單, 收尾之後的位置)；括號沒收尾回 (None, start)。"""
    try:
        inner, end = _group(text, start, ")", True)
    except Unbalanced:
        return None, start
    return _split_args(inner), end


def file_calls(text):
    """PowerShell 一段裡的 .NET 檔案呼叫：回 [(動作, [參數原樣])]，動作是 delete｜write｜replace｜move｜copy。
    [IO.FileInfo]::new('<p>').Delete()、(Get-Item '<p>').Delete() 回 ("delete", [路徑參數])。"""
    out = []
    for m in _NET_CALL.finditer(text):
        action = _NET_METHODS.get(m.group(1).lower())
        args, _end = _call_args(text, m.end())
        if action is None or args is None:
            continue
        if action == "open":
            nums = any(a.strip().strip("'\"") in allowed for a, allowed in zip(args[1:3], _OPEN_WRITE_NUM))
            if not nums and not any(_OPEN_WRITE_MODE.search(a) for a in args[1:]):
                continue
            action = "write"
        out.append((action, args))
    for m in _NET_NEW.finditer(text):
        args, end = _call_args(text, m.end())
        if args is not None and _DELETE_AFTER.match(text, end):
            out.append(("delete", args))
    for m in _GET_ITEM_GROUP.finditer(text):
        try:
            inner, end = _group(text, m.start() + 1, ")", True)
        except Unbalanced:
            continue
        if _DELETE_AFTER.match(text, end):
            out.append(("delete", _get_item_path(raw_words(inner, True)[1:])))
    return out


def _get_item_path(words):
    """Get-Item 的路徑參數：第一個位置參數，或 -Path:<p>、-LiteralPath:<p> 冒號後的值（-Path <p> 的值就是下一個位置參數）。"""
    for w in words:
        if w.startswith("-"):
            name, colon, value = w.partition(":")
            if colon and value and name.lower() in ("-path", "-literalpath", "-lp", "-pspath"):
                return [_bare_literal(value)]
            continue
        return [_bare_literal(w)]
    return []


_PS_NOT_LITERAL = frozenset("$@,{}()`")  # 裸字有這些就不是一個字面路徑（變數、陣列逗號、運算式、反引號）


def _bare_literal(word):
    """PowerShell 指令參數的裸字（Get-Item C:/x/a.txt）也是字面路徑：包成單引號；有引號、變數、陣列逗號、括號、反引號的照原樣。"""
    if word[:1] in "'\"" or any(c in _PS_NOT_LITERAL for c in word):
        return word
    return "'" + word.replace("'", "''") + "'"


def office_save_targets(text):
    """Office 附著後帶路徑的另存（.SaveAs、.SaveAs2、.SaveCopyAs、.ExportAsFixedFormat）的目標是字面路徑時，回那些路徑：
    第一個參數、Excel ExportAsFixedFormat(<型別>, <路徑>) 的第二個、python 的 FileName=／OutputFileName=。
    PowerShell 雙引號裡有 $ 或反引號的會展開，不算字面。"""
    out = []
    for m in _OFFICE_TARGET.finditer(text):
        value = m.group(1) if m.group(1) is not None else m.group(2)
        if m.group(2) is not None and ("$" in value or "`" in value):
            continue
        out.append(value)
    return out


def strip_file_calls(text):
    """把 .NET 檔案靜態呼叫、[IO.FileInfo]::new(…)、Office 另存連同括號裡的參數換成空白：同一段的全名寫入動詞從剩下的字抽目標
    （測試第 1 輪後裁定 13；.NET 呼叫另外由 file_calls 判）。回 (拿掉之後的字, 拿掉的那些字)。"""
    spans = []
    for rx in (_NET_CALL, _NET_NEW, OFFICE_SAVE):
        for m in rx.finditer(text):
            args, end = _call_args(text, m.end())
            if args is not None:
                spans.append((m.start(), end))
    for m in _GET_ITEM_GROUP.finditer(text):  # (Get-Item '<p>').Delete()
        try:
            _inner, end = _group(text, m.start() + 1, ")", True)
        except Unbalanced:
            continue
        after = _DELETE_AFTER.match(text, end)
        if after:
            _args, end2 = _call_args(text, after.end())
            spans.append((m.start(), end2 if _args is not None else after.end()))
    out, removed, last = [], [], 0
    for a, b in sorted(spans):
        if a < last:
            continue
        out.append(text[last:a] + " ")
        removed.append(text[a:b])
        last = b
    out.append(text[last:])
    return "".join(out), "\n".join(removed)


DOWNLOAD_VERBS = {"iwr", "irm", "invoke-webrequest", "invoke-restmethod", "curl", "wget"}


def outfile_targets(text, powershell=True):
    """iwr、irm、Invoke-WebRequest、Invoke-RestMethod、PowerShell 的 curl、wget 帶 -OutFile（含縮寫與 -OutFile:值）的字面目標；
    變數或運算式不算。不是這些動詞、或不是 PowerShell（Bash 的 curl、wget 沒有 -OutFile）回 []（測試第 1 輪後裁定 11）。"""
    if not powershell:
        return []
    words = raw_words(text, powershell)
    if verb_of(words) not in DOWNLOAD_VERBS:
        return []
    out = []
    for k, w in enumerate(words):
        name, colon, value = w.partition(":")
        if not name.lower().startswith("-outf"):
            continue
        raw = value if colon and value else (words[k + 1] if k + 1 < len(words) else "")
        v, _quoted, dyn = _unquote(raw, powershell)
        if v and not dyn:
            out.append(v)
    return out


def literal_arg(arg):
    """一個參數是字面字串時回裡面的字（PowerShell 單引號的 '' 是一個 '）；變數、運算式、雙引號裡有 $ 或反引號回 None。"""
    if len(arg) >= 2 and arg[0] == arg[-1] == "'" and "'" not in arg[1:-1].replace("''", ""):
        return arg[1:-1].replace("''", "'")
    if len(arg) >= 2 and arg[0] == arg[-1] == '"' and not any(c in arg[1:-1] for c in '"$`'):
        return arg[1:-1]
    return None


def list_only(verb, words):
    """D-185：這段只列檔名（ls、dir、tree、Get-ChildItem；find 只帶 -name／-type／-maxdepth）或只切目錄、印目前目錄。"""
    if verb in CD_ONLY_VERBS:
        return True
    if verb not in LIST_ONLY_VERBS:
        return False
    if verb == "find":
        flags = [w.lower() for w in words if w.startswith("-")]
        return all(f in FIND_LIST_FLAGS for f in flags)
    return True


# 開檔（D-514）：開的是文件、資料夾或網址照讀判；開的是程式時 guard 把「程式名加參數」那行當內層指令判
OPEN_VERBS = {"start-process", "saps", "invoke-item", "ii", "start", "explorer", "open", "xdg-open"}
# 文件白名單：巨集檔（.docm .xlsm .pptm .dotm .xltm）刻意不收
OPEN_DOC_EXT = frozenset(
    ".pdf .doc .docx .xls .xlsx .csv .ppt .pptx .odt .ods .odp .rtf .txt .md .log .json .xml .html .htm .png .jpg .jpeg "
    ".gif .bmp .tif .tiff .webp .heic .svg .mp3 .wav .m4a .mp4 .mov .avi .zip .msg .eml .dwg .dxf .stp .step".split())
OPEN_URL = re.compile(r"^(?:https?|mailto):", re.IGNORECASE)
OPEN_VERB_FLAGS = {"open", "edit", "print", "openasreadonly"}
# PowerShell 參數名可縮寫（照前綴認，認不出或不只一種就當看不出來）；值是那個參數的用處，"switch" 不帶值、"" 帶值但不管
_PS_COMMON = {"erroraction": "", "ea": "", "warningaction": "", "wa": "", "informationaction": "", "infa": "",
              "errorvariable": "", "ev": "", "warningvariable": "", "wv": "", "informationvariable": "", "iv": "",
              "outvariable": "", "ov": "", "outbuffer": "", "ob": "", "pipelinevariable": "", "pv": "",
              "verbose": "switch", "vb": "switch", "debug": "switch", "db": "switch"}
_SAPS_PARAMS = {"filepath": "file", "pspath": "file", "path": "file", "argumentlist": "args", "args": "args",
                "workingdirectory": "cwd", "verb": "verb", "redirectstandardoutput": "out", "redirectstandarderror": "out",
                "redirectstandardinput": "in", "windowstyle": "", "credential": "", "runas": "", "environment": "",
                "wait": "switch", "nonewwindow": "switch", "nnw": "switch", "passthru": "switch", "usenewenvironment": "switch",
                "loaduserprofile": "switch", "lup": "switch", **_PS_COMMON}
_II_PARAMS = {"path": "file", "literalpath": "file", "pspath": "file", "lp": "file", "filter": "", "include": "", "exclude": "",
              "credential": "", "whatif": "switch", "wi": "switch", "confirm": "switch", "cf": "switch", **_PS_COMMON}
_PS_MODULE = "microsoft.powershell.management\\"
_CMD_SWITCH = re.compile(r"^/[a-z]+$", re.IGNORECASE)
_CMD_META = set("&|<>^")  # cmd 會把這些當串接與導向
_PCT_VAR = re.compile(r"%[A-Za-z_]\w*%")


def raw_words(text, powershell=False):
    """照引號把一段拆成字，引號原樣留著（開檔段要知道哪個字有引號）。PowerShell 的陣列（'a', 'b'，逗號前後有空白）併成一個字。"""
    words, buf, quote, i, n = [], [], None, 0, len(text)
    esc = "`" if powershell else "\\"
    while i < n:
        ch = text[i]
        if ch == esc and i + 1 < n and quote != "'":
            buf.append(text[i:i + 2])
            i += 2
            continue
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch.isspace():
            if buf:
                words.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        words.append("".join(buf))
    if not powershell:
        return words
    merged = []
    for w in words:
        if merged and (merged[-1].endswith(",") or w.startswith(",")):
            merged[-1] += w
        else:
            merged.append(w)
    return merged


def _unquote(word, powershell):
    """原樣的字去引號：回 (值, 有引號, 展不開)。展不開：引號外或雙引號裡的變數與 $(…)、引號外的 ( )、PowerShell 開頭的 @、
    Bash 的反引號、任何地方的 %X%（cmd 會展開；網址的 %E4% 不算）、引號沒收尾。"""
    out, quote, quoted, dyn, i, n = [], None, False, False, 0, len(word)
    esc = "`" if powershell else "\\"
    while i < n:
        ch = word[i]
        if quote == "'":
            if ch == "'" and powershell and word.startswith("''", i):
                out.append("'")
                i += 2
                continue
            if ch == "'":
                quote = None
            else:
                out.append(ch)
            i += 1
            continue
        if ch == esc and i + 1 < n and (powershell or quote is None or word[i + 1] in '"\\$`'):
            out.append(word[i + 1])
            i += 2
            continue
        if ch == '"' and quote == '"':
            if powershell and word.startswith('""', i):
                out.append('"')
                i += 2
                continue
            quote = None
            i += 1
            continue
        if ch in "\"'" and quote is None:
            quote, quoted = ch, True
            i += 1
            continue
        if ch == "$" and i + 1 < n and (not powershell or word[i + 1].isalnum() or word[i + 1] in "_{(:"):
            dyn = True
        elif quote is None and (ch in "()" or (powershell and ch == "@" and i == 0)):
            dyn = True
        elif ch == "`" and not powershell:
            dyn = True
        out.append(ch)
        i += 1
    value = "".join(out)
    if _PCT_VAR.search(value) and not OPEN_URL.match(value):
        dyn = True
    return value, quoted, dyn or quote is not None


def _elements(word):
    """PowerShell 的陣列字（'a','b'）照引號外的逗號拆成元素，元素原樣（引號還在）。"""
    parts, buf, quote, i, n = [], [], None, 0, len(word)
    while i < n:
        ch = word[i]
        if ch == "`" and i + 1 < n and quote != "'":
            buf.append(word[i:i + 2])
            i += 2
            continue
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p for p in parts if p]


def _quote_arg(value):
    """接成一行時含空白的加雙引號（跟 Start-Process 一樣）；本來就有雙引號的照原樣。"""
    return f'"{value}"' if (not value or any(c.isspace() for c in value)) and '"' not in value else value


def _verb_word(value):
    """這個字是裸名稱（explorer、explorer.exe、Start-Process、Microsoft.PowerShell.Management\\Start-Process）就回動詞；
    帶路徑的（C:\\x\\explorer.exe）回 None。"""
    low = value.lower()
    if low.startswith(_PS_MODULE):
        low = low[len(_PS_MODULE):]
    if "/" in low or "\\" in low:
        return None
    return low[:-4] if low.endswith(".exe") else low


def _ps_role(name, table):
    if name in table:
        return table[name]
    roles = {role for key, role in table.items() if key.startswith(name)}
    return roles.pop() if name and len(roles) == 1 else None


def _ps_params(words, table):
    """PowerShell cmdlet 的參數：回 (具名 {用處: [原樣的值]}, 位置參數, 認不出)。有引號的 '-x' 是字串、不是參數名。"""
    named, positional, bad, k = {}, [], False, 0
    while k < len(words):
        w = words[k]
        if w.startswith("-") and len(w) > 1:
            name, colon, value = w[1:].partition(":")
            role = None if w.startswith("--") else _ps_role(name.lower(), table)
            if role is None:
                bad = True  # --%、認不出、縮寫不只一種：PowerShell 會報錯，守門當看不出來
            elif role != "switch":
                if not colon:
                    if k + 1 >= len(words):
                        bad = True
                        break
                    k += 1
                    value = words[k]
                named.setdefault(role, []).append(value)
            k += 1
            continue
        positional.append(w)
        k += 1
    return named, positional, bad


def _openable(target, base):
    """網址（http、https、mailto）、磁碟上存在的夾、副檔名在 OPEN_DOC_EXT 的檔。最後一段帶 : 的（x.bat:.docx）不算。"""
    if OPEN_URL.match(target):
        return True
    try:
        if os.path.isdir(normalize(target, base) if base else target):
            return True
    except (OSError, ValueError):
        pass
    last = re.split(r"[\\/]", target)[-1]
    return ":" not in last and os.path.splitext(last)[1].lower() in OPEN_DOC_EXT


def _start_process(rest, powershell):
    named, positional, opaque = _ps_params(rest, _SAPS_PARAMS)
    file_raw = named.get("file", [None])[0]
    if file_raw is None and positional:
        file_raw = positional.pop(0)
    args_raw = named.get("args", [None])[0]
    if args_raw is None and positional:
        args_raw = positional.pop(0)
    opaque = opaque or bool(positional) or any(len(named.get(r, ())) > 1 for r in ("file", "args", "cwd", "verb"))

    def values(raws):
        nonlocal opaque
        out = []
        for raw in raws:
            v, _q, dyn = _unquote(raw, powershell)
            opaque = opaque or dyn
            out.append(v)
        return out

    targets = [v for v in values([file_raw] if file_raw is not None else []) if v]
    elements = values(_elements(args_raw) if (args_raw is not None and powershell) else [args_raw] if args_raw else [])
    args_line = elements[0] if len(elements) == 1 else " ".join(_quote_arg(e) for e in elements)
    cwd = values(named.get("cwd", [])[:1])
    verb = values(named.get("verb", [])[:1])
    program = " ".join(x for x in (_quote_arg(targets[0]) if targets else "", args_line) if x) or None
    return {"targets": targets, "program": program, "verb_flag": verb[0] if verb else None,
            "writes": values(named.get("out", [])), "reads": values(named.get("in", [])),
            "cwd": cwd[0] if cwd else None, "opaque": opaque}


def _cmd_start(rest, powershell):
    """cmd 的 start：跳過開關與 /d <夾>；第一個空的或含空白的引號字是視窗標題（Git Bash 與 PowerShell 傳給 cmd 時只有這兩種帶引號）；
    之後第一個字是要開的，其餘是它的參數。cmd 會再拆一次，任何字裡有 & | < > ^ 就看不出來。"""
    cwd, title, target, params, opaque, k = None, False, None, [], False, 0
    while k < len(rest):
        raw = rest[k]
        v, quoted, dyn = _unquote(raw, powershell)
        opaque = opaque or dyn or bool(_CMD_META & set(v)) or '"' in v  # 值裡的引號 cmd 會再認一次（'"x.pdf"' 是標題）
        k += 1
        if target is not None:
            params.append(raw)
            continue
        sw = v[1:] if not powershell and v.startswith("//") else v  # Git Bash 的 //b 是 /b
        low = sw.lower()
        if low == "/d" and k < len(rest):
            cwd, _q, dyn = _unquote(rest[k], powershell)
            opaque = opaque or dyn
            k += 1
        elif low.startswith("/d") and len(low) > 2 and (powershell or low[2] != "/"):
            cwd = sw[2:]
        elif low in ("/node", "/affinity"):
            k += 1
        elif _CMD_SWITCH.match(low):
            pass
        elif not title and quoted and (not v or any(c.isspace() for c in v)):
            title = True
        else:
            target = v
    program = " ".join([_quote_arg(target)] + params) if target else None
    return {"targets": [target] if target else [], "program": program, "verb_flag": None, "writes": [], "reads": [],
            "cwd": cwd, "opaque": opaque}


def _positional_targets(rest, powershell, table=None):
    """Invoke-Item（table 給參數表）、explorer、open、xdg-open 要開的東西。"""
    targets, opaque = [], False
    if table is not None:
        named, positional, opaque = _ps_params(rest, table)
        raws = [e for raw in named.get("file", []) + positional for e in (_elements(raw) if powershell else [raw])]
    else:
        raws = list(rest)
    k = 0
    while k < len(raws):
        v, _q, dyn = _unquote(raws[k], powershell)
        opaque = opaque or dyn
        k += 1
        parts = v.split(",")
        if table is None and _CMD_SWITCH.match(parts[0]):
            targets += [p for p in parts[1:] if p and not _CMD_SWITCH.match(p)]  # explorer /select,<路徑>
        elif table is None and v.startswith("-"):
            if v in ("-a", "-b") and k < len(raws):  # open -a <程式>：程式也是要開的東西
                targets.append(_unquote(raws[k], powershell)[0])
                k += 1
            else:
                opaque = True  # open --、open -e、xdg-open --help…：後面接的未必是要開的檔
        elif v:
            targets.append(v)
    return {"targets": targets, "program": None, "verb_flag": None, "writes": [], "reads": [], "cwd": None, "opaque": opaque}


def open_stage(verb, words, powershell=False, base=None):
    """D-514 開檔段。verb 是這段的動詞（verb_of），words 是 raw_words 拆的字（引號原樣），base 是效用 cwd（判夾用）。
    不是開檔動詞回 None；是的話回 {"targets", "kind", "program", "verb_flag", "writes", "reads", "cwd", "opaque", "exe"}，
    路徑是原字串；exe 是動詞用完整路徑寫時的那個程式檔（裸名稱是 None）。
    PowerShell 的 start 是 Start-Process 的別名；Bash 的 start 與 cmd /c start 照 cmd 的規則。"""
    if verb not in OPEN_VERBS and verb != "cmd":
        return None
    values = [_unquote(w, powershell)[0] for w in words]
    idx = next((k for k, v in enumerate(values) if _base(v) == verb), None)
    if idx is None:
        return None
    # 完整路徑寫的（C:\Windows\explorer.exe、…\cmd.exe）照同一個動詞判，那個程式檔交 guard 查 run_file（測試第 1 輪裁定 11）
    exe = values[idx] if _verb_word(values[idx]) is None else None
    rest = words[idx + 1:]
    if verb == "cmd":
        k = 0
        while k < len(rest) and values[idx + 1 + k].lower() not in ("/c", "//c") and re.match(
                r"^//?[a-z](?::\S*)?$", values[idx + 1 + k], re.IGNORECASE):
            k += 1  # /s /q /d /v:on 這類開關
        if k + 1 >= len(rest) or values[idx + 1 + k].lower() not in ("/c", "//c") or _verb_word(values[idx + 2 + k]) != "start":
            return None
        got = _cmd_start(rest[k + 2:], powershell)
    elif verb == "start" and not powershell:
        got = _cmd_start(rest, powershell)
    elif verb in ("start-process", "saps", "start"):
        got = _start_process(rest, powershell)
    elif verb in ("invoke-item", "ii"):
        got = _positional_targets(rest, powershell, _II_PARAMS)
    else:
        got = _positional_targets(rest, powershell)
    kind = "open" if all(_openable(t, base) for t in got["targets"]) else "run"
    if got["verb_flag"] is not None and got["verb_flag"].lower() not in OPEN_VERB_FLAGS:
        kind = "run"
    program = got["program"]
    if program is None and got["targets"]:
        program = _quote_arg(next((t for t in got["targets"] if not _openable(t, base)), got["targets"][0]))
    got.update(kind=kind, program=program if kind == "run" else None, exe=exe)
    return got


REDIRECT = re.compile(r">")
# 引號外不能出現的字元：串接（; & |）、導向（< >）、換行、子運算式（( )，含 $( 與 PowerShell 的 @( ）；反引號在引號裡也會執行
SHELL_META = set(";&|<>()\n\r")
_TOKEN_BREAK = set(";|&<>()")
_URL = re.compile(r"^(?!file:)\w+://")
_OPT_PREFIX = re.compile(r"^(?:--?[\w-]+=|(?:of|if)=)")  # dd 的 of=、if= 也是路徑（第十輪 A4，密碼與守門補）
_FILENAME = re.compile(r"\.\w{1,6}$")
# 第九輪 Z4：不存在的裸 token 只有這些副檔名才算新檔（v1.2、example.com 不算）
_NEW_FILE_EXT = re.compile(r"\.(?:md|txt|docx|doc|xlsx|xls|csv|pdf|pptx|json|yaml|yml|ini|log|html|png|jpg|jpeg|zip)$", re.IGNORECASE)
_GLOB_CHARS = re.compile(r"[*?\[]")  # 第十輪 A6：是不是萬用字元 token 先過 is_wildcard，這只找第一個萬用字元的位置；大括號先經 brace_expand
_BRACE = re.compile(r"\{([^{}]*,[^{}]*)\}")  # 第十輪 A1：{a,b} 展開
BRACE_LIMIT = 20
# 第十輪 A5：展不開的變數——整個 token 是 $X、${X}、$X/…、$X\…（PowerShell 的 $env:X 也在內）；%X%、%X%\… 另算
_VAR_WHOLE = re.compile(r"^\$(?:\{(?P<b>[A-Za-z_][\w:]*)\}|(?P<n>[A-Za-z_][\w:]*))(?:[\\/].*)?$")
_PCT_WHOLE = re.compile(r"^%[A-Za-z_]\w*%(?:[\\/].*)?$")
_VAR_INSIDE = re.compile(r"\$\{?[A-Za-z_]|%[A-Za-z_]\w*%")
PS_AUTO_VARS = {"false", "true", "null", "_", "psitem", "args", "lastexitcode"}  # PowerShell 自動變數不是展不開的路徑；$? 本來就不像變數名
_ABS_START = re.compile(r"^(?:[A-Za-z]:|/|\\\\|~|file:)")
_CD = {"cd", "pushd", "set-location", "sl", "chdir"}
# token 裡夾的絕對路徑：C:\…、C:/…、/c/…
ABS_PATH = re.compile(r"(?:(?<![A-Za-z0-9_])[A-Za-z]:[\\/]|(?<![\w.~-])/[a-zA-Z]/)[^\"'\s|;&<>()]*")
_PYTHON = re.compile(
    r"^\s*(\"[^\"]+\"|'[^']+'|\S+)(?:\s+-(?:[IuBsE]+|X\s+\S+))*\s+(\"[^\"]+\"|'[^']+'|\S+)(?:\s|$)",
    re.IGNORECASE,
)
_BARE_INTERP = {"python", "python3", "py", "python.exe", "python3.exe", "py.exe"}
# bash 的跳脫：\ 後接非 ASCII 或空白時，\ 只是跳脫（薪\資 → 薪資、AI\ 產出 → AI 產出）
_ESCAPE = re.compile(r"\\(?=[^\x00-\x7f]| )")


def outside_quotes(cmd, powershell=False):
    """去掉引號裡的內容（引號裡的 > ; | 都只是文字）。引號沒有成對時回 None。
    Bash：引號外與雙引號裡 \\ 跳脫下一個字（\\" 與 \\' 不是引號邊界，N-a）；單引號裡沒有跳脫，\\ 是普通字（測試 28 F2-1）；$'…' 裡 \\ 跳脫。
    PowerShell：\\ 一律是普通字，跳脫是反引號（引號外與雙引號裡）；單引號裡的 '' 照關了再開算。"""
    esc = "`" if powershell else "\\"
    out, quote, i, n = [], None, 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if quote:
            if ch == esc and quote in ('"', "$'") and i + 1 < n:
                i += 2
                continue
            if ch == quote[-1]:
                quote = None
        elif ch == esc and i + 1 < n:
            out.append(cmd[i:i + 2])
            i += 2
            continue
        elif not powershell and cmd.startswith("$'", i):
            quote = "$'"
            out.append("$")
            i += 2
            continue
        elif ch in "\"'":
            quote = ch
        else:
            out.append(ch)
        i += 1
    return None if quote else "".join(out)


def redirect_joint(cmd, i):
    """cmd[i] 是導向的一部分、不是串接或管線（測試 28 F2-2）：>| 的 |、>& 的 &、&> 的 &。
    丟到 fd 的 >&1、>&- 先被 DISCARD_OUTPUT 拿掉了，剩下的 >& 後面都是檔。"""
    ch = cmd[i]
    if ch == "|":
        return i > 0 and cmd[i - 1] == ">"
    if ch == "&":
        return (i > 0 and cmd[i - 1] == ">") or (cmd.startswith("&>", i) and not (i > 0 and cmd[i - 1] == "&"))
    return False


def norm_cmd(cmd):
    """小寫、\\\\ 與 \\ 一律改成 /。"""
    return cmd.lower().replace("\\\\", "/").replace("\\", "/")


# Windows 開關式指令：只有這些（或 PowerShell）後面的單字母 /x 才是旗標；bash 的 grep -r X /c 裡 /c 是 C 槽根（第七輪 X3）
SWITCH_VERBS = {"cmd", "cmd.exe", "findstr", "robocopy", "xcopy", "dir", "attrib", "icacls"}


def escaped_break(cmd, i):
    """Bash：cmd[i] 是 \\，後面接 ; | & < > ( ) 或換行——跳脫成普通字（find 的 \\;）或續行，不是一個叫 \\ 的路徑（測試 28 N4）。"""
    return cmd[i] == "\\" and i + 1 < len(cmd) and (cmd[i + 1] in _TOKEN_BREAK or cmd[i + 1] in "\r\n")


def _tokens(cmd, powershell=False):
    """回 [(字串, 有引號, 緊接在 > 後面, 是不是一段的第一個 token, 第一個引號字元或空字串)]。引號裡的空白與符號保留；引號沒成對就一路到底。"""
    out, buf, quote, quoted, after_redirect, first, qchar = [], [], None, False, False, True, ""

    def flush():
        nonlocal buf, quoted, after_redirect, first, qchar
        if buf or quoted:
            out.append(("".join(buf), quoted, after_redirect, first, qchar))
            after_redirect = False
            first = False
        buf, quoted, qchar = [], False, ""

    prev, skip = "", 0
    for i, ch in enumerate(cmd):
        if skip:
            skip -= 1
            prev = ch
            continue
        if quote:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
        elif ch in "\"'":
            quote = ch
            quoted = True
            qchar = qchar or ch
        elif not powershell and escaped_break(cmd, i):
            skip = 1  # find 的 \;、行尾 \ 續行：單獨的 \ 不是磁碟根（測試 28 N4）
            if cmd[i + 1] not in "\r\n":
                buf.append(cmd[i + 1])
            elif cmd.startswith("\r\n", i + 1):
                skip = 2
        elif ch == " " and prev == "\\":
            buf.append(ch)  # 跳脫的空白是 token 的一部分（AI\ 產出），N-c 再去掉 \
        elif redirect_joint(cmd, i):
            flush()
            after_redirect = True  # >|、>&檔、&>檔：後面那個是導向目標，不是新的一段（F2-2）
        elif ch.isspace() or ch in _TOKEN_BREAK:
            flush()
            if ch == ">":
                after_redirect = True
            elif ch in _TOKEN_BREAK:
                after_redirect = False
                first = True  # ; | & ( ) 之後是新的一段
        else:
            buf.append(ch)
        prev = ch
    flush()
    return out


def brace_expand(text, limit=BRACE_LIMIT):
    """第十輪 A1：token 含 {a,b} 就展開成各組合（cat ../raw/行政/{薪資,x}/s.xlsx、rm -rf ../r{2,3}），上限 limit 組；沒逗號的大括號不動。"""
    out = [text]
    while True:
        nxt, changed = [], False
        for t in out:
            m = _BRACE.search(t)
            if not m:
                nxt.append(t)
                continue
            changed = True
            nxt.extend(t[:m.start()] + alt + t[m.end():] for alt in m.group(1).split(","))
        out = nxt[:limit]
        if not changed or len(nxt) > limit:
            return out


def brace_head(text, powershell=False):
    """第十一輪保底：token 含 { 就回 { 之前的資料夾（跟原守門 eba223d 的萬用字元前綴夾一樣）——有分隔符取最後一個夾，沒有就是 .；
    { 在開頭（ForEach-Object { $_ }）或前面是 $（${X}）回 None；前面是 @ 只有 PowerShell 才回 None（@{a=1} 是雜湊表，第十二輪），
    Bash 的 @{…,…} 照走保底。展開截斷（超過上限）、{N..M} 不展開都不影響這個保底。"""
    idx = text.find("{")
    if idx <= 0:
        return None
    head = text[:idx]
    if head[-1] == "$" or (powershell and head[-1] == "@"):
        return None
    if "/" in head or "\\" in head:
        folder = re.split(r"[\\/]", head)
        folder = "/".join(folder[:-1]) if "/" in head else "\\".join(folder[:-1])
        return folder or ("/" if head.startswith("/") else None)
    return "."


def is_wildcard(text, unquoted=None):
    """第十輪 A6：萬用字元候選要含 *，或同時含 ?／[ 與路徑分隔符且不是網址（://）。
    unquoted：只算引號外的字元（Bash 引號裡的 * ? [ 是文字，Q7）；分隔符與網址看整個 token。"""
    chars = text if unquoted is None else unquoted
    if "*" in chars:
        return True
    return ("?" in chars or "[" in chars) and ("/" in text or "\\" in text) and "://" not in text


def _bare_under(text, base):
    """第九輪 Z2：裸 token（原樣或去跳脫）接效用 cwd 後存在就是路徑（rm -rf 案子、mv 收件匣 /tmp/）。"""
    for form in dict.fromkeys((text, _ESCAPE.sub("", text))):
        try:
            if os.path.exists(normalize(form, base)):
                return True
        except (OSError, ValueError):
            continue
    return False


def _candidates(text, quoted, after_redirect, after_cd, switch_ok=False, write_base=None):
    """一個 token 裡可能是路徑的字串。夾在中間的絕對路徑先抽；沒有就看整個 token 像不像路徑。
    write_base：寫入指令裡的裸 token（不是段動詞）要看效用 cwd——存在的算路徑，不存在的要已知副檔名才算新檔（第八輪裁定 1、第九輪 Z2 Z4）。"""
    if not text or _URL.match(text):
        return []
    if text in (".", ".."):
        return [text]  # 不帶斜線的 . 與 .. 也是路徑，要經區判斷（骨架與來源登記第三輪 S1：grep -r X .. 走 Z10b）
    if switch_ok and re.match(r"^/[a-zA-Z]$", text):
        return []  # cmd /c、findstr /s 的單字母開關不是路徑（第六輪 V5；第七輪 X3：只對開關式指令或 PowerShell）
    matches = list(ABS_PATH.finditer(text))
    if matches and matches[0].start() > 0:
        # 夾在中間的絕對路徑（python -c "open('C:/…')"、P=C:/…）
        return [m.group(0).rstrip(",;:)'\"") for m in matches if "$" not in m.group(0) and "%" not in m.group(0)]
    if matches:
        return [] if ("$" in text or "%" in text) else [text]  # 整個 token 就是路徑（引號裡可含空白）
    if "$" in text or "%" in text:
        return []
    if text.startswith("-") and not _OPT_PREFIX.match(text):
        return []
    if "/" in text or "\\" in text or after_redirect:
        return [_OPT_PREFIX.sub("", text)]
    if (quoted or after_cd) and _FILENAME.search(text):
        return [text]
    if write_base is not None and (_bare_under(text, write_base) or _NEW_FILE_EXT.search(text)):
        return [text]
    return []


def _relative_resolves(cand, path):
    """B3：相對 token 要解析後存在，或父目錄存在且最後一段像檔名（有副檔名或無空白）。含萬用字元的看前綴資料夾存不存在。"""
    m = _GLOB_CHARS.search(path) if is_wildcard(path) else None
    if m:
        head = path[:m.start()]
        head = head.rsplit("/", 1)[0] if "/" in head else head
        return bool(head) and os.path.isdir(head)
    try:
        if os.path.exists(path):
            return True
        parent, _, last = path.rpartition("/")
        return bool(parent) and os.path.isdir(parent) and bool(_FILENAME.search(last) or " " not in last)
    except (OSError, ValueError):
        return False


# 直譯器與殼放程式碼的旗標（D-186 (c)）：後面那個參數是程式碼，不是路徑，只抽裡面的絕對路徑
CODE_FLAGS = {"-c", "-e", "--eval", "-p", "--print", "-r", "-command", "/c", "/k"}


_LONE_DRIVE_DIR = re.compile(r"^/[A-Za-z]/$")
_OPERAND_BEFORE = re.compile(r"[\w)\]]\s*$")
_OPERAND_AFTER = re.compile(r"^\s*[\w(]")


def code_paths(text):
    """程式碼或 heredoc 內文裡的絕對路徑（D-186 (c)(d)）：引號裡的整段（路徑可含空白），與引號外的 C:\\…、C:/…、/c/…。
    相對路徑不抽（程式碼裡的字串多半不是路徑；以前整段程式碼被當成相對路徑，會分到實體夾，B-093 3-2d、card-maker (f)）。
    B-425：單獨的 /（'/'.join、s='/'）與算式的 /（5 /c/ 2：前後都是數字、括號、變數名）不是路徑，不抽；真的路徑照抽。"""
    found = []
    for m in re.finditer(r"([\"'])([^\"'\n]+)\1", text):
        s = m.group(2).strip()
        if s != "/" and _ABS_START.match(s) and not s.startswith("~") and not _URL.match(s):
            found.append(s)
    for m in ABS_PATH.finditer(text):
        s = m.group(0).rstrip(",;:)'\"")
        if (_LONE_DRIVE_DIR.match(s) and _OPERAND_BEFORE.search(text[:m.start()])
                and _OPERAND_AFTER.match(text[m.start() + len(s):])):
            continue  # 算式：x /c/ 2
        if not any(s in f for f in found):
            found.append(s)
    return [f for f in found if "$" not in f and "%" not in f]


def _walk(command, cwd, powershell=False):
    """依序產出 (原字串, 正規化路徑)，cd 之後的相對路徑接 cd 的目標。powershell：單字母 /x 一律當旗標（PowerShell 沒有 Git Bash 寫法）。
    直譯器與殼的 -c、-e、-Command 後面那個參數是程式碼：只抽裡面的絕對路徑（D-186 (c)）。"""
    cleaned = DISCARD_OUTPUT.sub(" ", command)
    base, expect_cd_target, after_cd, verb = cwd, False, False, ""
    writing = is_write(command, powershell)  # 寫入指令裡像檔名的裸 token（cp /tmp/a 新檔.md）也當路徑，要經區判斷（骨架與來源登記第八輪）
    prev = ""
    for token, quoted, after_redirect, first, _qchar in _tokens(cleaned, powershell):
        code_arg = (verb_of([verb]) in INTERPRETERS or verb_of([verb]) in SHELL_WRAPPERS) and prev.lower() in CODE_FLAGS and not first
        prev = token
        if first:
            verb = token.lower().replace("\\", "/").rsplit("/", 1)[-1]
        if code_arg:
            for cand in code_paths(token):
                yield cand, normalize(cand, base)
            continue
        # 第十一輪保底：{ 之前的資料夾一律是候選，經區判斷（Z10／Z10b／上層）；排在展開之後，展開命中的理由（readonly_source）比前綴夾具體
        head = brace_head(token, powershell) if "{" in token and not (first and not expect_cd_target) else None
        head_p = normalize(head, base) if head is not None else None
        if expect_cd_target:
            if token.startswith("-"):
                continue  # Set-Location -Path X、cd -- X：旗標跳過，下一個才是目標
            expect_cd_target = False
            if token and "$" not in token and "%" not in token:
                forms = brace_expand(token)  # cd ../r{2,3}：每個展開都是目標，效用 cwd 取第一個（A1）
                targets = [normalize(form, base) for form in forms]
                base = targets[0]
                after_cd = True
                for form, target in zip(forms, targets):
                    yield form, target
                if head is not None and (_ABS_START.match(head) or _relative_resolves(head, head_p)):
                    yield head, head_p
                continue
        if token.lower() in _CD:
            expect_cd_target = True
            continue
        for text in brace_expand(token):  # 第十輪 A1：{a,b} 先展開再抽
            for cand in _candidates(text, quoted, after_redirect, after_cd, powershell or verb in SWITCH_VERBS,
                                    write_base=(base if writing and not first else None)):
                # N-c：原樣，與去掉「\ 後接非 ASCII 或空白」跳脫的形態（bash 把 薪\資 讀成 薪資）兩種都比對；
                # PowerShell 的 \ 不是跳脫字元，只比原樣（測試 28：C:\…\網站密碼.xlsx 多產假路徑被擋）
                for form in dict.fromkeys((cand,) if powershell else (cand, _ESCAPE.sub("", cand))):
                    p = normalize(form, base)
                    if _ABS_START.match(form) or _relative_resolves(form, p):
                        yield form, p
        if head is not None and (_ABS_START.match(head) or _relative_resolves(head, head_p)):
            yield head, head_p


def extract_paths(command, cwd, powershell=False):
    """抽路徑：引號內字串、含 / 或 \\ 的 token、導向目標、token 裡夾的絕對路徑；去符號後正規化。變數（$X、%X%）不算。"""
    found = []
    for _raw, p in _walk(command, cwd, powershell):
        if p not in found:
            found.append(p)
    return found


def glob_tokens(command, cwd):
    """含萬用字元（* ? [ {）的 token，正規化後回傳（保留萬用字元），給呼叫端 glob 展開。"""
    out = []
    for raw, p in _walk(command, cwd):
        if is_wildcard(raw) and p not in out:
            out.append(p)
    return out


def glob_prefixes(command, cwd):
    """含萬用字元的 token，回萬用字元前最後一個資料夾（正規化）。shell 會展開它，守門要看它蓋到哪。"""
    out = []
    for raw, _p in _walk(command, cwd):
        m = _GLOB_CHARS.search(raw) if is_wildcard(raw) else None
        if not m:
            continue
        head = raw[:m.start()].replace("\\", "/")
        head = head.rsplit("/", 1)[0] if "/" in head else "."
        out.append(normalize(head, cwd) if head != "." else normalize(cwd, None))
    return out


def unresolved_paths(command, powershell=False):
    """第九輪 Z3、第十輪 A5：展不開的變數路徑 token——(a) 整個 token 是 $X、${X}、$X/…、$X\\…，或 %X%、%X%\\…（裸變數，第九輪裁定 3）；
    (b) 帶變數又含 / 或 \\ 的、或是導向目標的 token（C:/raw/$X、> "out$Y"）。單引號裡（Bash 與 PowerShell 都不展開）、網址、
    丟掉輸出的 >$null、| Out-Null，與 PowerShell 自動變數（$false $true $null $_ $PSItem $args $LASTEXITCODE）不算；
    +%Y%m%d、--format=%H、-Confirm:$false 這些不是整個 token 是變數的也不算。呼叫端對寫入指令有就 deny unparsed（I-031）；讀取指令不看。"""
    out = []
    for text, _quoted, after_redirect, _first, qchar in _tokens(DISCARD_OUTPUT.sub(" ", command), powershell):
        if not text or qchar == "'" or _URL.match(text):
            continue
        m = _VAR_WHOLE.match(text)
        if m:
            name = (m.group("b") or m.group("n")).lower()
            if powershell and name in PS_AUTO_VARS:
                continue
            out.append(text)
        elif _PCT_WHOLE.match(text):
            out.append(text)
        elif _VAR_INSIDE.search(text) and ("/" in text or "\\" in text or after_redirect):
            out.append(text)
    return out


def is_write(command, powershell=False):
    """引號外有 > 或寫入動詞；有包裝指令（bash -c、cmd /c、powershell -c…）時整句都看；程式碼裡的 open(…,'w') 之類引號內也算。
    導到 null 不算。"""
    cleaned = DISCARD_OUTPUT.sub(" ", command)
    bare = outside_quotes(cleaned, powershell)
    wrapped = bare is None or WRAPPER.search(bare)
    scope = cleaned if wrapped else bare
    return bool(REDIRECT.search(scope) or WRITE_WORDS.search(scope) or WRITE_CODE.search(cleaned)
                or (wrapped and WRAPPED_OPEN.search(scope))
                or ((wrapped or powershell) and OFFICE_SAVE.search(scope))  # 守門補洞 #25：Office 另存、實例刪除
                or (powershell and PS_INSTANCE_DELETE.search(scope)))


def _interpreter_ok(token):
    """S1：直譯器只接受裸字 python／python3／py（可帶 .exe）。任何含路徑分隔符的直譯器一律不算（第二輪 S1 殘）。"""
    return token.lower() in _BARE_INTERP


CURLY_QUOTES = frozenset(chr(c) for c in range(0x2018, 0x2020))  # ‘’‚‛“”„‟


# 引號外會被殼展開的字（D-212：認不出實際的參數）：Bash 的變數、萬用字元、大括號、~；PowerShell 的變數、@ 展開、逗號陣列
_BARE_UNCERTAIN = {False: set("$*?[{~"), True: set("$@,{(")}


def shell_words(command, powershell):
    """照工具的引號規則把一句拆成字（給單一 kb 呼叫認參數，D-212）：回 [(字, 不確定)]；引號沒成對回 None。
    不確定：字裡有會被展開的東西（引號外的 $、萬用字元…，雙引號裡的 $），實際傳給程式的是什麼看不出來。
    呼叫端先過 is_single_plugin_call（引號外沒有反斜線、反引號、串接與導向），這裡只處理引號。"""
    words, buf, uncertain, have, i, n = [], [], False, False, 0, len(command)
    while i < n:
        ch = command[i]
        if ch.isspace():
            if have:
                words.append(("".join(buf), uncertain))
            buf, uncertain, have = [], False, False
            i += 1
            continue
        have = True
        if ch == "'":
            j = i + 1
            while True:
                k = command.find("'", j)
                if k < 0:
                    return None
                buf.append(command[j:k])
                if powershell and command.startswith("''", k):
                    buf.append("'")
                    j = k + 2
                    continue
                i = k + 1
                break
            continue
        if ch == '"':
            j = i + 1
            while True:
                if j >= n:
                    return None
                c = command[j]
                if c == '"':
                    if powershell and command.startswith('""', j):
                        buf.append('"')
                        j += 2
                        continue
                    break
                if not powershell and c == "\\" and j + 1 < n and command[j + 1] in '"\\$`\n':
                    buf.append(command[j + 1])
                    j += 2
                    continue
                if c == "$":
                    uncertain = True
                buf.append(c)
                j += 1
            i = j + 1
            continue
        if ch in _BARE_UNCERTAIN[powershell]:
            uncertain = True
        buf.append(ch)
        i += 1
    if have:
        words.append(("".join(buf), uncertain))
    return words


_SEG_BREAK = {False: set(";&|()\n\r`"), True: set(";&|(){}\n\r")}


def segments(command, powershell, comments=False):
    """#50：在引號外的串接、換行、管線、括號處切開，回每一段的文字；引號沒成對回 None。只給守門找「整句裡有沒有密碼動作」用。
    comments=True 另外照殼的規則拿掉註解（# 到行尾；PowerShell 另有 <# … #>），只用來找出是哪個動作好講理由：
    拿得比殼多會吃掉真的指令，所以這種切法的結果只能讓守門多問、不能讓它不問（審查：{#、全形空白、$'\\'' 都會錯位）。"""
    out, buf, quote, i, n = [], [], None, 0, len(command)
    esc = "`" if powershell else "\\"
    while i < n:
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == esc and quote == '"' and i + 1 < n:
                buf.append(command[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == esc and i + 1 < n:
            buf.append(command[i:i + 2])
            i += 2
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        at_word = i == 0 or command[i - 1].isspace() or command[i - 1] in ";&|(){}"
        if comments and powershell and command.startswith("<#", i) and at_word:
            j = command.find("#>", i + 2)
            i = n if j < 0 else j + 2
            buf.append(" ")
            continue
        if comments and ch == "#" and at_word:
            j = command.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch in _SEG_BREAK[powershell]:
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if quote:
        return None
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def comment_cuts(command, tool=None):
    """#50：單一 kb 呼叫裡可能是註解開頭的位置（引號外、前面是空白類字元的 #），由前到後。給得比殼寬：
    殼不當註解時 kb 收到 #… 會拆不過、不執行，所以每個位置都截一次去判斷只會多問。"""
    cuts = set()
    for powershell in ((False,) if tool == "Bash" else (True,) if tool == "PowerShell" else (False, True)):
        esc = "`" if powershell else "\\"
        quote, i, n = None, 0, len(command)
        while i < n:
            ch = command[i]
            if quote:
                if ch == esc and quote == '"' and i + 1 < n:
                    i += 2
                    continue
                if ch == quote:
                    quote = None
                i += 1
                continue
            if ch in "'\"":
                quote = ch
            elif ch == "#" and i and command[i - 1].isspace():
                cuts.add(i)
            i += 1
    return sorted(cuts)


def kb_argv(command, tool=None):
    """單一 kb 呼叫傳給 kb.py 的參數（腳本之後的字）：回 [(字, 不確定)]；拆不出來回 None。
    tool 不知道時 Bash、PowerShell 兩種拆法要一樣，不一樣的字算不確定。"""
    results = []
    for powershell in ((False,) if tool == "Bash" else (True,) if tool == "PowerShell" else (False, True)):
        words = shell_words(command, powershell)
        if not words:
            return None
        k = 1
        while k < len(words) and words[k][0].startswith("-"):
            k += 2 if words[k][0] == "-X" else 1  # python -I -u -X utf8 kb.py …
        if powershell and any(w == "--%" for w, _u in words):
            words = [(w, True) for w, _u in words]  # #50 審查：PowerShell 的 --% 停止解析，kb 收到的參數跟這裡拆的不同
        results.append(words[k + 1:])
    argv = results[0]
    if len(results) > 1 and results[0] != results[1]:
        argv = [(w, True) for w, _u in argv]
    return argv


def is_single_plugin_call(command, plugin_root, cwd=None, tool=None):
    """整句是不是只有「python <plugin>/scripts/kb.py …」這一個呼叫。串接、導向、子運算式、反引號都不算；
    引號外出現任何反斜線也不算（N-a：kb.py 路徑在引號內，用不到跳脫）。"""
    if "`" in command or "$(" in command:
        return False
    if tool != "Bash" and CURLY_QUOTES & set(command):
        return False  # PowerShell 把彎引號當引號，outside_quotes 看不出來；Bash 裡是普通字（manage 第二版第四輪）；不知道是哪個照 PowerShell
    # 引號照工具的規則看；不知道是哪個工具就兩種都要過（測試 28 F2-1：'a\' 在 Bash 是關了的引號）
    for powershell in ((False,) if tool == "Bash" else (True,) if tool == "PowerShell" else (False, True)):
        bare = outside_quotes(command, powershell)
        if bare is None or "\\" in bare or any(ch in SHELL_META for ch in bare):
            return False
        if not powershell and "$'" in command:
            return False  # $'…' 的跳脫規則不同，kb 呼叫用不到
    m = _PYTHON.match(command)
    if not m:
        return False
    cwd = cwd or os.getcwd()
    if not _interpreter_ok(m.group(1).strip("\"'")):
        return False
    script = m.group(2).strip("\"'")
    return normalize(script, cwd) == normalize(os.path.join(str(plugin_root), KB_SCRIPT), cwd)
