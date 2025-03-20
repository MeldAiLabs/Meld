import bpy
import re
import textwrap

from .logging_utils import log

# Regex to find <BLENDER_CODE> blocks, capturing the content inside
BLENDER_CODE_REGEX = re.compile(r"<BLENDER_CODE>(.*?)</BLENDER_CODE>", re.DOTALL)
# Regex for simple inline bold and italic (non-greedy)
# Matches **text** or *text*
INLINE_FORMAT_REGEX = re.compile(r"(\*\*.*?\*\*|\*.*?\*)")
DEFAULT_WRAP_WIDTH = 80  # Default width for text wrapping


def draw_blender_code(layout, code_text: str):
    """Draws a block specifically for Blender Python code."""
    box = layout.box()  # Outer box for the code block
    header_row = box.row(align=True)
    header_row.label(text="Blender Code:", icon='SCRIPTPLUGINS')
    # Add a copy button
    op = header_row.operator("wm.copy_to_clipboard", text="", icon='COPYDOWN')
    op.text_to_copy = code_text

    # Draw the code lines within an inner box for slight indentation
    code_box = box.box()
    lines = code_text.strip().split('\n')
    for line in lines:
        # Simple label for each line. Monospace would be ideal but harder.
        code_box.label(text=line)


def draw_header(layout, line: str, width: int):
    """Draws a markdown header (# syntax)."""
    level = 0
    for char in line:
        if char == '#':
            level += 1
        else:
            break

    text = line[level:].strip()
    if not text:
        return  # Ignore empty headers

    wrapped_lines = textwrap.fill(text, width=width).split('\n')

    for i, wrapped_line in enumerate(wrapped_lines):
        row = layout.row()
        # Add icon only to the first line of the header
        icon = 'RIGHTARROW_THIN' if i == 0 else 'BLANK1'
        row.label(text=wrapped_line, icon=icon)


def draw_bullet(layout, line: str, width: int):
    """Draws a markdown bullet point (-, *, +, •)."""
    # Determine indentation
    stripped_line = line.lstrip()
    indent_level = len(line) - len(stripped_line)
    # Assuming 2 spaces per indent level for wrap calculation
    indent_chars = indent_level // 2

    # Extract bullet type and text content
    first_char = stripped_line[0]

    # Handle both Unicode bullets and markdown bullets
    if first_char in ['-', '*', '+', '•']:
        # Skip the bullet and any following space
        text_start = 1
        while text_start < len(stripped_line) and stripped_line[text_start].isspace():
            text_start += 1
        text = stripped_line[text_start:].strip()
    else:
        text = stripped_line.strip()

    # Wrap text considering indentation
    # Reduce width by ~2 chars per indent level + 2 for bullet/space
    effective_width = max(10, width - (indent_chars * 2) - 2)

    # Special handling for long lines - use textwrap's fill with subsequent_indent
    wrapper = textwrap.TextWrapper(
        width=effective_width,
        initial_indent='',
        subsequent_indent=' ' * 2,  # Indent wrapped lines
        break_long_words=True,
        break_on_hyphens=True
    )
    wrapped_lines = wrapper.fill(text).split('\n')

    for i, wrapped_line in enumerate(wrapped_lines):
        row = layout.row()
        # Add indentation spaces
        for _ in range(indent_chars):
            row.label(text="", icon='BLANK1')  # Spacer

        # Add bullet icon or spacer for subsequent lines
        if i == 0:  # First line gets the bullet
            row.label(text=wrapped_line, icon='DOT')
        else:  # Subsequent lines get indented
            row.label(text=wrapped_line, icon='BLANK1')


def _draw_inline_formatted_text(row_layout, text: str):
    """Draws a single line of text, processing inline **bold** and *italic*."""
    # Temporarily disable special formatting to fix spacing issues
    # Just render the entire text as is in a single label
    row_layout.label(text=text)

    # Original code with special formatting (commented out)
    """
    last_end = 0
    for match in INLINE_FORMAT_REGEX.finditer(text):
        start, end = match.span()
        # Draw text before the match
        if start > last_end:
            row_layout.label(text=text[last_end:start])

        # Process the matched segment
        segment = match.group(1)
        if segment.startswith("**") and segment.endswith("**"):
            # Draw bold text (without asterisks)
            # Note: True bold styling isn't easy with standard labels.
            # Consider custom drawing or specific UI elements if needed.
            bold_label = row_layout.label(text=segment[2:-2])
            # bold_label.scale_x = 1.1 # Subtle attempt at emphasis? Might not work reliably.
        elif segment.startswith("*") and segment.endswith("*"):
            # Draw italic text (without asterisks)
            # Even harder to make visually distinct
            italic_label = row_layout.label(text=segment[1:-1])
        else:
            # Should not happen with current regex, but fallback
            row_layout.label(text=segment)

        last_end = end

    # Draw any remaining text after the last match
    if last_end < len(text):
        row_layout.label(text=text[last_end:])
    """


def draw_paragraph(layout, text: str, width: int):
    """Draws a standard paragraph with text wrapping and inline formatting."""
    if not text.strip():  # Handle empty lines as spacing
        return

    wrapped_lines = textwrap.fill(text, width=width).split('\n')
    for line in wrapped_lines:
        row = layout.row()
        _draw_inline_formatted_text(row, line)


def draw_markdown(layout, markdown_string: str, width: int = DEFAULT_WRAP_WIDTH):
    """
    Parses a string containing mixed markdown and <BLENDER_CODE> blocks,
    and draws it to the given UI layout, line by line.
    """
    log(f"Rendering markdown (width={width}, first 50 chars): {markdown_string[:50]}...", "DEBUG")

    lines = markdown_string.strip().split('\n')
    in_code_block = False
    code_buffer = []

    i = 0
    while i < len(lines):
        line = lines[i]

        if line.strip() == "<BLENDER_CODE>":
            in_code_block = True
            code_buffer = []  # Start collecting code
            i += 1
            continue

        if line.strip() == "</BLENDER_CODE>":
            if in_code_block:
                draw_blender_code(layout, "\n".join(code_buffer))
                in_code_block = False
                code_buffer = []
            else:
                # Stray closing tag? Draw it as normal text.
                draw_paragraph(layout, line, width)
            i += 1
            continue

        if in_code_block:
            code_buffer.append(line)
        else:
            # Not in a code block, check for markdown elements
            stripped_line = line.lstrip()
            if stripped_line.startswith('#'):
                draw_header(layout, line, width)
            # Check for any type of bullet point (markdown or Unicode)
            elif (stripped_line.startswith(('-', '*', '+', '•')) and
                  (len(stripped_line) == 1 or stripped_line[1].isspace())):
                draw_bullet(layout, line, width)
            # Simple check for === or --- header syntax (must be immediate next line)
            elif i + 1 < len(lines) and lines[i + 1].strip() and all(c == '=' for c in lines[i + 1].strip()) and line.strip():
                draw_header(layout, "# " + line, width)  # Treat as H1
                i += 1  # Skip the === line
            elif i + 1 < len(lines) and lines[i + 1].strip() and all(c == '-' for c in lines[i + 1].strip()) and line.strip():
                draw_header(layout, "## " + line, width)  # Treat as H2
                i += 1  # Skip the --- line
            else:
                # Default to paragraph
                draw_paragraph(layout, line, width)

        i += 1

    # If the string ended while inside a code block (malformed input)
    if in_code_block:
        log("Warning: Markdown ended unexpectedly inside a <BLENDER_CODE> block.", "WARNING")
        draw_blender_code(layout, "\n".join(code_buffer))

    log("Finished rendering markdown", "DEBUG")


# Example usage (for testing if run directly, though not typical in Blender addons)
if __name__ == "__main__":
    # This block won't run in Blender normally
    test_string = """
# Main Header
This is the first paragraph. It should wrap nicely.

## Sub Header

Here is some text *after* the code block.
It can contain **bold** text too.

- Bullet point 1
- Bullet point 2
  - Indented bullet point 2.1 which is quite long and definitely needs to be wrapped correctly.
  - Indented bullet point 2.2
* Another bullet style
+ And another one

<BLENDER_CODE>
import bpy
print("Hello from Blender Code!")
bpy.ops.mesh.primitive_cube_add()
</BLENDER_CODE>

Another Header
===

Yet Another Header
---

Final text.
"""
    # In a real scenario, you'd get the layout from a draw() method
    # This is just illustrative:

    class MockLayout:
        _indent = 0
        def label(self, text="", icon='NONE'): print(f"{'  ' * self._indent}LABEL: '{text}' (icon={icon})")
        def box(self): print(f"{'  ' * self._indent}BOX START"); self._indent += 1; return self  # Crude indent
        def row(self, align=False): print(f"{'  ' * self._indent}ROW START (align={align})"); return self
        def operator(self, *args, **kwargs): print(f"{'  ' * self._indent}OPERATOR: {args} {kwargs}"); return self
        def separator_spacer(self): print(f"{'  ' * self._indent}SEPARATOR_SPACER")
        def separator(self, factor=1.0): print(f"{'  ' * self._indent}SEPARATOR (factor={factor})")

        # Need context manager methods for box
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self._indent -= 1
            print(f"{'  ' * self._indent}BOX END")

    print("--- MOCK RENDER START ---")
    mock_layout = MockLayout()
    draw_markdown(mock_layout, test_string, width=40)
    print("--- MOCK RENDER END ---")
