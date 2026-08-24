"""HatsOff CLI AI response Markdown renderer"""

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
import re

def get_console_width():
    """Get the current console width, capped at 160 characters."""
    return min(160, Console().width)


def print_banner():
    """Prints the HatsOff banner"""

    console2 = Console(width=get_console_width(), legacy_windows=False)
    banner_text = (f"""
        ██╗  ██╗ █████╗ ████████╗███████╗ ██████╗ ███████╗███████╗
        ██║  ██║██╔══██╗╚══██╔══╝██╔════╝██╔═══██╗██╔════╝██╔════╝
        ███████║███████║   ██║   ███████╗██║   ██║█████╗  █████╗
        ██╔══██║██╔══██║   ██║   ╚════██║██║   ██║██╔══╝  ██╔══╝
        ██║  ██║██║  ██║   ██║   ███████║╚██████╔╝██║     ██║
        ╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   ╚══════╝ ╚═════╝ ╚═╝     ╚═╝
              HatsOff — offensive AI for labs & pentests
                         by Tahir
    """)
    console2.print(Panel(banner_text, subtitle="( ㉿ HatsOff )", border_style="blue", padding=(1, 2)))


def parse_n_print_response(
    api_response_text: str,
    *,
    title: str = "( HatsOff Response )",
    border_style: str = "blue",
):
    """Robust Markdown renderer for ALL GenAI response types"""
    console = Console(width=get_console_width(), legacy_windows=False)

    # Clean excessive newlines
    cleaned = re.sub(r'\n\s*\n\s*\n', '\n\n', (api_response_text or "").strip())

    try:
        # Rich's Markdown handles 95% of cases perfectly
        md = Markdown(cleaned, code_theme="github-dark", inline_code_theme="github-dark")
        console.print(Panel(md, title=title, border_style=border_style, padding=(1, 2)))
        return True

    except Exception:
        # Fallback for malformed MD
        pass

    # Advanced fallback parser
    lines = cleaned.split('\n')
    in_code = False
    code_buffer = []
    lang = "text"

    for line in lines:
        line = line.rstrip()

        # Code blocks (ALL languages)
        if line.strip().startswith('```'):
            if in_code:
                # End block
                code_content = ''.join(code_buffer).strip()
                if code_content:
                    syntax = Syntax(f"\n{code_content}\n", lang, theme="github-dark", line_numbers=True)
                    console.print(syntax)
                code_buffer, in_code, lang = [], False, "text"
            else:
                # Start new code block
                lang = line.strip()[3:].strip() or "text"
                in_code = True
            continue

        if in_code:
            code_buffer.append(line + '\n')
            continue

        # Headers (H1 - H6)
        header_match = re.match(r'^(#{1,6})\s+(.+)', line)
        if header_match:
            level = len(header_match.group(1))
            title = header_match.group(2).strip()
            colors = ['magenta', 'cyan', 'yellow', 'green', 'blue', 'red']
            color = colors[min(level - 1, 5)]
            console.print(f"[bold {color}] {title}[/bold {color}]")
            continue

        # Tables
        if re.match(r'^\s*\|.*\|$', line):  # Lines starting & ending with | (allowing leading whitespace)
            console.print(f"[dim white]{line}[/dim white]")
            continue

        # Enhanced inline formatting
        line = re.sub(r'\*\*(.+?)\*\*', r'[bold white]\1[/bold white]', line)
        line = re.sub(r'\*(.+?)\*', r'[italic]\1[/italic]', line)
        line = re.sub(r'`([^`]+)`', r'[bright_blue]\1[/bright_blue]', line)
        line = re.sub(r'\[(https?://[^\s\]]+)\]\(([^\)]+)\)', r'[underline blue]\2[/underline blue]', line)

        # Lists (unlimited numbers)
        if re.match(r'^\s*[-\*+•]\s', line):
            line = re.sub(r'^\s*[-\*+•]\s', '[yellow]•[/yellow] ', line)
        elif re.match(r'^\s*\d+\.', line):
            line = re.sub(r'^\s*(\d+)\.\s', r'[cyan]\1.[/cyan] ', line)

        console.print(line)

        # Handle final code block if exists
        if in_code and code_buffer:
            code_content = ''.join(code_buffer).strip()
            if code_content:
                syntax = Syntax(f"\n{code_content}\n", lang, theme="github-dark", line_numbers=True)
                console.print(syntax)

    return True



# Usage example (optional)
if __name__ == "__main__":
    test_md = """
# H1 Header

## H2 Subheader

### H3 subheader

**Bold** and *italic* `code` [link](https://example.com)

- Bullet list
- Another item
1. Numbered list
10. Works for any number!

| Col1 | Col2 | Col3 |
|------|------|------|
| A    | B    | C    |

```python
def hello():
    print("World!")
```


| Feature | Status | Benefit |
|---------|--------|---------|
| **Rich Markdown** | ✅ Primary | Native tables/math/links support |
| **Code Languages** | ✅ 100+ langs | `json`, `yaml`, `sql`, `javascript`, etc. |
| **Tables** | ✅ Improved | Proper pipe table detection |
| **Links** | ✅ Added | `[text](URL)` rendering |
| **Lists** | ✅ Unlimited | `1.`, `10.`, `100.` all work |
| **Headers** | ✅ H1-H6 | Color-coded by level |
| **Error Handling** | ✅ Bulletproof | Never crashes |
| **Terminal Size** | ✅ Adaptive | Works any screen size |
"""

    # parse_n_print_response(test_md)
    print_banner()