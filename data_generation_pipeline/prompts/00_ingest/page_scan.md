You look at one page of a document and list every visual element that a team could reuse as an image: charts, plots, diagrams, flowcharts, maps, photos, and tables (grids of numbers or text). You give a rough position for each; exact edges are computed later from the page geometry.
---USER---
Page {{page}} of {{n_pages}}. Text printed on the page (for reading captions and credit lines; layout unreliable):
<<<
{{page_text}}
>>>

List every visual element on the attached page image. For each:
- kind: "figure" (any chart — bar, line, pie, stacked — plot, diagram, flowchart, map, screenshot; a chart stays a figure even when its values are printed on the bars), "table" (a grid of cells with ruled rows/columns of text or numbers), "photo" (a photograph), or "decorative" (logo, icon, cover art, page ornament, a picture with no information content).
- values_printed: true if numbers (values, percentages) are printed on the element itself (data labels on a chart, cells of a table).
- caption: the caption or title printed for it, verbatim ('' if none).
- description: one sentence of what it shows.
- box: [left, top, right, bottom] as percentages of the page width/height (0-100), covering the element itself and its axis labels / legend, but not the caption and not the running text.
- third_party: true if a credit line ("Source:", "Photo:", "©", "Courtesy") names a photo agency or an organisation other than the document's own publisher.
Skip running text, headers, footers and page numbers. An empty page returns {"items": []}.
Return {"items": [{"kind", "caption", "description", "box", "third_party", "values_printed"}]}.
