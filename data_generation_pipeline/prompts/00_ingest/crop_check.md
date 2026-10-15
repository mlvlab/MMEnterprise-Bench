You check whether an automatically cropped region of a PDF page contains exactly one complete {{kind}} and nothing else.
---USER---
The crop is supposed to contain this {{kind}}:
"{{caption}}"

Look at the attached image and report:
- contains: "figure", "table", "mixed" (figure and table), or "none" (only body paragraphs / nothing relevant)
- complete: is the whole {{kind}} inside the crop (nothing cut off at an edge)?
- extra_text_top / extra_text_bottom: do body paragraphs of running text (not axis labels, legends, or table cells) appear at the top or bottom?
- cut_top / cut_bottom: is the {{kind}} itself cut off at the top or bottom edge?
- running_header: is there a page header, page number, author list or running title (e.g. '6   A. Author et al.') at the very top or bottom of the crop?
- note: one short sentence.
