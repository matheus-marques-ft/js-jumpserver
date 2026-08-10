import difflib
import re


def no_special_chars(s):
    return bool(re.match(r'\w+$', s))


def safe_str(s):
    return s.encode('utf-8', errors='ignore').decode('utf-8')


def get_text_diff(old_text, new_text):
    diff = difflib.unified_diff(
        old_text.splitlines(), new_text.splitlines(), lineterm=""
    )
    return "\n".join(diff)


def color_fmt(msg, color=None):
    # ANSI color codes
    colors = {
        'red': '\033[91m',
        'green': '\033[92m',
        'yellow': '\033[93m',
        'blue': '\033[94m',
        'purple': '\033[95m',
        'cyan': '\033[96m',
        'default': '\033[0m'  # default value to end the color
    }

    # Get the color code; if no color is specified or it's unsupported, use the default color
    color_code = colors.get(color, colors['default'])
    # Print the colored message
    return f"{color_code}{msg}{colors['default']}"  # ensure the color resets after the message ends


def color_print(msg, color=None):
    print(color_fmt(msg, color))


def color_fill_print(tmp, msg, color=None):
    text = tmp.format(color_fmt(msg, color))
    print(text)
