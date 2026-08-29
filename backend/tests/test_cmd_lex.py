"""cmd.exe 语义词法回归测试（无第三方依赖，直接运行：python test_cmd_lex.py）。

覆盖 _cmd_lex / _cmd_split_shell_segments / _validate_shell_command / _needs_shell
在 Windows cmd.exe 语义下的安全切分正确性，锁定「校验词法 == 执行语义」约定：
  - `^` 转义 → 字面量，不再误切成新命令（修复误拦）
  - `\\` 非转义 → `\\&` / `\\|` 是真实分隔符，第二命令基命令必须校验（修复绕过）
  - 换行/回车是命令分隔符（修复 `dir\\n evil` 分段绕过）
  - `%VAR%` 变量名整体；区间内含元字符时立即切分（修复 `%a&evil%` 绕过）
  - 单引号是普通字符（非引号），其中的 `|&` 是真实分隔符
  - fd 重定向 `2>` / `2>>` / `2>&1` 留在本命令段
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from app.tools.file_tools import (
    _cmd_lex,
    _cmd_split_shell_segments,
    _needs_shell,
    _validate_shell_command,
)

_PASS = 0
_FAIL = 0


def _expect(label, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}")


def _segments(cmd):
    return _cmd_split_shell_segments(cmd)


def _validates(cmd):
    try:
        _validate_shell_command(cmd, cwd=".")
        return True
    except Exception:
        return False


def test_caret_escape_not_separator():
    # cmd 中 ^| / ^& 是字面量，不能切成新命令（修复误拦）
    _expect("^| 不切段", _segments('echo hello^|world') == [['echo', 'hello|world']])
    _expect("^& 不切段", _segments('echo a^&b') == [['echo', 'a&b']])
    _expect("^> 是字面量非重定向", _cmd_lex('echo hi ^> x') == [('echo', 'word'), ('hi', 'word'), ('>', 'word'), ('x', 'word')])
    _expect("^| 命令通过校验", _validates('echo hello^|world'))
    _expect("^& 命令通过校验", _validates('echo a^&b'))


def test_backslash_is_not_escape():
    # cmd 中 \ 不是转义符 → \& / \| 是真实分隔符，第二命令必须校验（修复绕过）
    _expect("\\& 切两段", _segments('echo safe\\&echo DANGER') == [['echo', 'safe\\'], ['echo', 'DANGER']])
    _expect("\\& 后 evil 被拦截", not _validates('echo safe\\&evilcmd'))
    _expect("\\| 切两段", _segments('echo safe\\|echo DANGER') == [['echo', 'safe\\'], ['echo', 'DANGER']])
    _expect("\\> 是真实重定向", _cmd_lex('echo hi \\> secret.txt')[-2:] == [('>', 'redirect'), ('secret.txt', 'word')])


def test_newline_is_command_separator():
    # 换行/回车等价于 & → 切段（修复 dir\nevil 分段绕过）
    _expect("换行切两段", _segments('dir\n evil') == [['dir'], ['evil']])
    _expect("换行后 evil 被拦截", not _validates('dir\n evilcmd'))
    _expect("CRLF 换行切段", _segments('echo a\r\necho b') == [['echo', 'a'], ['echo', 'b']])
    _expect("换行后白名单命令放行", _validates('echo x\necho y'))


def test_percent_var():
    _expect("正常 %VAR% 整体一个 token", _segments('echo %USERPROFILE%') == [['echo', '%USERPROFILE%']])
    _expect("%VAR% 通过校验", _validates('echo %USERPROFILE%'))
    # 变量名区间内含元字符（cmd 变量名不允许）→ 立即切分，防绕过
    _expect("%a&evil% 切段并拦截", not _validates('echo %a&evilcmd%'))
    # 引号内 %VAR% 同样展开 → 需要 shell
    _expect("引号内 %VAR% 需要 shell", _needs_shell('echo "%PATH%"'))


def test_single_quote_is_literal():
    # cmd 中单引号是普通字符，不是引号 → \| 是真实管道
    _expect("单引号内管道切两段", _segments("echo 'x|y'") == [["echo", "'x"], ["y'"]])
    _expect("单引号内管道第二段 y' 被拦截", not _validates("echo 'x|y'"))


def test_fd_redirects():
    _expect("2>&1 不产生新命令段", _segments('echo x 2>&1') == [['echo', 'x', '2', '>&', '1']])
    _expect("2>&1 通过校验", _validates('echo x 2>&1'))
    _expect("2> 重定向留在本段", _segments('cmd /c dir 2> err.txt') == [['cmd', '/c', 'dir', '2>', 'err.txt']])
    _expect("普通 > 重定向留在本段", _segments('echo hi > out.txt') == [['echo', 'hi', '>', 'out.txt']])


def test_normal_pipe_redir():
    _expect("管道切两段", _segments('git log | head -5') == [['git', 'log'], ['head', '-5']])
    _expect("管道后 evil 拦截", not _validates('echo hi | evilcmd'))
    _expect("&& 切两段", _segments('echo a && echo b') == [['echo', 'a'], ['echo', 'b']])
    _expect("; 单词分隔不出段", _segments('echo a; echo b') == [['echo', 'a', 'echo', 'b']])


def main():
    test_caret_escape_not_separator()
    test_backslash_is_not_escape()
    test_newline_is_command_separator()
    test_percent_var()
    test_single_quote_is_literal()
    test_fd_redirects()
    test_normal_pipe_redir()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
